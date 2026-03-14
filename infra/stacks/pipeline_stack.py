"""
infra/stacks/pipeline_stack.py

All AWS resources for the PaceRoute travel blog pipeline.
Fargate task runs daily at 09:00 UTC via EventBridge Scheduler.
Files and DB persist on EFS at /mnt/efs.
"""
import os
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_efs as efs,
    aws_iam as iam,
    aws_logs as logs,
    aws_secretsmanager as sm,
    aws_scheduler as scheduler,
)
from constructs import Construct


# Secret names created manually once in Secrets Manager
SECRET_NAMES = {
    "ANTHROPIC_API_KEY":   "paceroute/pipeline/anthropic_api_key",
    "DATAFORSEO_LOGIN":    "paceroute/pipeline/dataforseo_login",
    "DATAFORSEO_PASSWORD": "paceroute/pipeline/dataforseo_password",
    "APIFY_API_TOKEN":     "paceroute/pipeline/apify_api_token",
    "UNSPLASH_ACCESS_KEY": "paceroute/pipeline/unsplash_access_key",
    "IDEOGRAM_API_KEY":    "paceroute/pipeline/ideogram_api_key",
    "WP_URL":              "paceroute/pipeline/wp_url",
    "WP_USERNAME":         "paceroute/pipeline/wp_username",
    "WP_APP_PASSWORD":     "paceroute/pipeline/wp_app_password",
}


class PipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── VPC ──────────────────────────────────────────────────────────────
        # 2 public subnets, no NAT gateway — Fargate tasks get public IPs.
        vpc = ec2.Vpc(
            self, "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
        )

        # ── ECR ──────────────────────────────────────────────────────────────
        repo = ecr.Repository(
            self, "EcrRepo",
            repository_name="paceroute-pipeline",
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(max_image_count=5)
            ],
        )

        # ── Security groups ───────────────────────────────────────────────────
        fargate_sg = ec2.SecurityGroup(
            self, "FargateSg",
            vpc=vpc,
            description="Fargate task outbound only",
            allow_all_outbound=True,
        )

        efs_sg = ec2.SecurityGroup(
            self, "EfsSg",
            vpc=vpc,
            description="EFS — allow NFS from Fargate",
            allow_all_outbound=False,
        )
        efs_sg.add_ingress_rule(
            peer=fargate_sg,
            connection=ec2.Port.tcp(2049),
            description="NFS from Fargate",
        )

        # ── EFS ───────────────────────────────────────────────────────────────
        filesystem = efs.FileSystem(
            self, "Efs",
            vpc=vpc,
            security_group=efs_sg,
            encrypted=True,
            removal_policy=RemovalPolicy.RETAIN,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        access_point = filesystem.add_access_point(
            "PipelineAp",
            path="/pipeline",
            create_acl=efs.Acl(owner_uid="1000", owner_gid="1000", permissions="750"),
            posix_user=efs.PosixUser(uid="1000", gid="1000"),
        )

        # ── CloudWatch log group ───────────────────────────────────────────────
        log_group = logs.LogGroup(
            self, "LogGroup",
            log_group_name="/ecs/paceroute-pipeline",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── IAM roles ─────────────────────────────────────────────────────────
        execution_role = iam.Role(
            self, "EcsExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )
        # Allow pulling secrets at task startup
        execution_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=["arn:aws:secretsmanager:*:*:secret:paceroute/pipeline/*"],
        ))

        task_role = iam.Role(
            self, "EcsTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        # Runtime secret access (if app calls SDK directly)
        task_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=["arn:aws:secretsmanager:*:*:secret:paceroute/pipeline/*"],
        ))
        # EFS access
        filesystem.grant_root_access(task_role)

        # ── ECS cluster ───────────────────────────────────────────────────────
        cluster = ecs.Cluster(
            self, "Cluster",
            cluster_name="paceroute-pipeline",
            vpc=vpc,
            container_insights=True,
        )

        # ── Secrets references ────────────────────────────────────────────────
        secrets = {
            env_key: ecs.Secret.from_secrets_manager(
                sm.Secret.from_secret_name_v2(self, f"Secret{env_key}", name)
            )
            for env_key, name in SECRET_NAMES.items()
        }

        # ── ECS task definition ───────────────────────────────────────────────
        task_def = ecs.FargateTaskDefinition(
            self, "TaskDef",
            family="paceroute-pipeline",
            cpu=1024,       # 1 vCPU
            memory_limit_mib=2048,
            execution_role=execution_role,
            task_role=task_role,
            volumes=[
                ecs.Volume(
                    name="efs-pipeline",
                    efs_volume_configuration=ecs.EfsVolumeConfiguration(
                        file_system_id=filesystem.file_system_id,
                        transit_encryption="ENABLED",
                        authorization_config=ecs.AuthorizationConfig(
                            access_point_id=access_point.access_point_id,
                            iam="ENABLED",
                        ),
                    ),
                )
            ],
        )

        container = task_def.add_container(
            "pipeline",
            image=ecs.ContainerImage.from_ecr_repository(repo, tag="latest"),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="pipeline",
                log_group=log_group,
            ),
            secrets=secrets,
            environment={
                "DB_PATH":    "/mnt/efs/pipeline.db",
                "OUTPUT_DIR": "/mnt/efs/output",
                "AUTO_PUBLISH": "true",
            },
        )
        container.add_mount_points(
            ecs.MountPoint(
                container_path="/mnt/efs",
                source_volume="efs-pipeline",
                read_only=False,
            )
        )

        # ── EventBridge Scheduler role ─────────────────────────────────────────
        scheduler_role = iam.Role(
            self, "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        scheduler_role.add_to_policy(iam.PolicyStatement(
            actions=["ecs:RunTask"],
            resources=[task_def.task_definition_arn],
        ))
        scheduler_role.add_to_policy(iam.PolicyStatement(
            actions=["iam:PassRole"],
            resources=[
                execution_role.role_arn,
                task_role.role_arn,
            ],
        ))

        # ── EventBridge Scheduler ─────────────────────────────────────────────
        # Subnet IDs needed at synth time; use Fn.select + Fn.split pattern.
        subnet_ids = [subnet.subnet_id for subnet in vpc.public_subnets]

        scheduler.CfnSchedule(
            self, "DailySchedule",
            schedule_expression="cron(0 9 * * ? *)",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="OFF"
            ),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=f"arn:aws:ecs:{self.region}:{self.account}:cluster/{cluster.cluster_name}",
                role_arn=scheduler_role.role_arn,
                ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                    task_definition_arn=task_def.task_definition_arn,
                    launch_type="FARGATE",
                    network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                        awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                            subnets=subnet_ids,
                            security_groups=[fargate_sg.security_group_id],
                            assign_public_ip="ENABLED",
                        )
                    ),
                    task_count=1,
                ),
            ),
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "EcrRepoUri",     value=repo.repository_uri)
        cdk.CfnOutput(self, "EfsId",          value=filesystem.file_system_id)
        cdk.CfnOutput(self, "ClusterName",    value=cluster.cluster_name)
        cdk.CfnOutput(self, "TaskDefinition", value=task_def.task_definition_arn)
        cdk.CfnOutput(self, "LogGroup",       value=log_group.log_group_name)
