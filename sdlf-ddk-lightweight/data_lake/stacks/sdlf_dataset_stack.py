# Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License").
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Any, Optional
from dataclasses import dataclass
from aws_ddk_core.base import BaseStack
import aws_cdk as cdk
from aws_cdk.custom_resources import Provider
from aws_ddk_core.resources import KMSFactory, SQSFactory
import aws_cdk.aws_glue as glue
import aws_cdk.aws_sqs as sqs
import aws_cdk.aws_iam as iam 
import aws_cdk.aws_kms as kms
import aws_cdk.aws_s3 as s3
import aws_cdk.aws_lakeformation as lf
import aws_cdk.aws_ssm as ssm


@dataclass
class SDLFDatasetConfig:
    team: str 
    dataset: str
    pipeline: str
    stage_a_transform: str
    stage_b_transform: str
    artifacts_bucket: s3.IBucket
    artifacts_bucket_key: kms.IKey
    stage_bucket: s3.IBucket
    stage_bucket_key: kms.IKey
    glue_role: iam.IRole
    register_provider: Provider

class SDLFDatasetStack(BaseStack):
    def __init__(self, scope, id: str, environment_id: str, resource_prefix: str, config: SDLFDatasetConfig, **kwargs: Any) -> None:
        super().__init__(scope, id, environment_id, **kwargs)

        self._environment_id: str = environment_id
        self._config: SDLFDatasetConfig = config
        self._team = self._config.team
        self._pipeline = self._config.pipeline
        self._dataset = self._config.dataset
        self._stage_a_transform = self._config.stage_a_transform
        self._stage_b_transform = self._config.stage_b_transform

        self._resource_prefix = resource_prefix

        glue_path = f"data_lake/src/glue/pyshell_scripts/sdlf_heavy_transform/{self._team}/{self._dataset}/main.py"

        self._crawler = self._create_dataset(
            team=self._team, 
            pipeline=self._pipeline, 
            dataset_name=self._dataset,
            path = glue_path,
            stage_a_transform=self._stage_a_transform,
            stage_b_transform=self._stage_b_transform
        )
    
        
    def _create_dataset(self, team: str, pipeline: str, dataset_name: str, path : str, stage_a_transform: Optional[str] = None, stage_b_transform: Optional[str] = None ) -> None:

        job: glue.CfnJob = glue.CfnJob(
            self,
            f"{self._resource_prefix}-heavy-transform-{team}-{dataset_name}-job",
            name=f"{self._resource_prefix}-{team}-{dataset_name}-glue-job",
            glue_version="2.0",
            allocated_capacity=2,
            execution_property=glue.CfnJob.ExecutionPropertyProperty(max_concurrent_runs=4),
            command=glue.CfnJob.JobCommandProperty(
                name="glueetl",
                script_location=f"s3://{self._config.artifacts_bucket.bucket_name}/{path}",
            ),
            default_arguments={"--job-bookmark-option": "job-bookmark-enable", "--enable-metrics": "", "--additional-python-modules": "awswrangler==2.4.0"},
            role=self._config.glue_role.role_arn,
        )

        lf.CfnDataLakeSettings(self, f"{self._resource_prefix}-{team}-{dataset_name}-DataLakeSettings",
            admins=[lf.CfnDataLakeSettings.DataLakePrincipalProperty(
                data_lake_principal_identifier=self._config.glue_role.role_arn
            )])

        self.stage_a_transform: str = stage_a_transform if stage_a_transform else "light_transform_blueprint"
        self.stage_b_transform: str = stage_b_transform if stage_b_transform else "heavy_transform_blueprint"

        self._props={
                "id":f"{team}-{dataset_name}",
                "description":f"{dataset_name.title()} dataset",
                "name": f"{team}-{dataset_name}",
                "type": "octagon_dataset",
                "pipeline": pipeline,
                "max_items_process": {
                    "stage_b": 100,
                    "stage_c": 100
                },
                "min_items_process" : {
                    "stage_b": 1,
                    "stage_c": 1
                },
                "version": 1,
                "transforms":{
                "stage_a_transform": self.stage_a_transform,
                "stage_b_transform": self.stage_b_transform,
            }
            }

        service_setup_properties = {"RegisterProperties": self._props}

        cdk.CustomResource(
            self,
            f"{self._props['id']}-{self._props['type']}-custom-resource",
            service_token=self._config.register_provider.service_token,
            properties=service_setup_properties
        )

        database: glue.CfnDatabase = glue.CfnDatabase(
            self,
            f"{self._resource_prefix}-{team}-{dataset_name}-database",
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=f"aws_datalake_{self._environment_id}_{team}_{dataset_name}_db",
                location_uri=f"s3://{self._config.stage_bucket.bucket_name}/post-stage/{team}/{dataset_name}"
            ),
            catalog_id=cdk.Aws.ACCOUNT_ID,
            
        )

        lf.CfnPermissions(
            self,
            f"{self._resource_prefix}-{team}-{dataset_name}-glue-job-database-lakeformation-permissions",
            data_lake_principal=lf.CfnPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=self._config.glue_role.role_arn
            ),
            resource=lf.CfnPermissions.ResourceProperty(
                database_resource=lf.CfnPermissions.DatabaseResourceProperty(name=database.ref)
            ),
            permissions=["CREATE_TABLE", "ALTER", "DROP"],
        )

        #SQS and DLQ
        #sqs kms key resource
        sqs_key = KMSFactory.key(
            self,
            id=f"{self._resource_prefix}-{team}-{dataset_name}-sqs-key-b",
            environment_id = self._environment_id,
            description=f"{self._resource_prefix} SQS Key Stage B",
            alias=f"{self._resource_prefix}-{team}-{dataset_name}-sqs-stage-b-key",
            enable_key_rotation=True,
            pending_window=cdk.Duration.days(30),
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        routing_dlq = sqs.DeadLetterQueue(
            max_receive_count=1, 
            queue=SQSFactory.queue(self, 
                            id=f'{self._resource_prefix}-{team}-{dataset_name}-dlq-b.fifo',
                            environment_id= self._environment_id,
                            queue_name=f'{self._resource_prefix}-{team}-{dataset_name}-dlq-b.fifo', 
                            fifo=True,
                            visibility_timeout=cdk.Duration.seconds(60),
                            encryption=sqs.QueueEncryption.KMS,
                            encryption_master_key=sqs_key))

        ssm.StringParameter(
            self,
            f'{self._resource_prefix}-{team}-{dataset_name}-dlq-b.fifo-ssm',
            parameter_name=f"/SDLF/SQS/{team}/{dataset_name}StageBDLQ",
            string_value=f'{self._resource_prefix}-{team}-{dataset_name}-dlq-b.fifo',
        )

        SQSFactory.queue(
            self, 
            id=f'{self._resource_prefix}-{team}-{dataset_name}-queue-b.fifo', 
            environment_id = self._environment_id,
            queue_name=f'{self._resource_prefix}-{team}-{dataset_name}-queue-b.fifo', 
            fifo=True,
            visibility_timeout=cdk.Duration.seconds(60),
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=sqs_key, 
            dead_letter_queue=routing_dlq)

        ssm.StringParameter(
            self,
            f'{self._resource_prefix}-{team}-{dataset_name}-queue-b.fifo-ssm',
            parameter_name=f"/SDLF/SQS/{team}/{dataset_name}StageBQueue",
            string_value=f'{self._resource_prefix}-{team}-{dataset_name}-queue-b.fifo',
        )

        # Glue Crawler
        crawler_role: iam.Role = iam.Role(
            self,
            f"{self._resource_prefix}-{team}-{dataset_name}-glue-crawler-role",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole")],
        )
        self._config.stage_bucket_key.grant_decrypt(crawler_role)
        self._config.stage_bucket.grant_read_write(crawler_role)

        lf.CfnPermissions(
            self,
            f"{self._resource_prefix}-{team}-{dataset_name}-glue-crawler-lf-permissions",
            data_lake_principal=lf.CfnPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=crawler_role.role_arn
            ),
            resource=lf.CfnPermissions.ResourceProperty(
                database_resource=lf.CfnPermissions.DatabaseResourceProperty(name=database.ref)
            ),
            permissions=["CREATE_TABLE", "ALTER", "DROP"],
        )

        crawler = glue.CfnCrawler(
            self,
            f"{self._resource_prefix}-{team}-{dataset_name}-crawler",
            name=f"{self._resource_prefix}-{team}-{dataset_name}-post-stage-crawler",
            database_name=database.ref,
            targets=glue.CfnCrawler.TargetsProperty(
                s3_targets=[glue.CfnCrawler.S3TargetProperty(path=f"s3://{self._config.stage_bucket.bucket_name}/post-stage/{team}/{dataset_name}")] 
            ),
            role=crawler_role.role_arn,
        )
        return crawler


    @property
    def database_crawler(self) -> glue.CfnCrawler:
        return self._crawler
