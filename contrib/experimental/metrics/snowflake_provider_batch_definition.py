
from typing import TypeVar, Union
from contrib.experimental.metrics.metric_provider import MPBatchDefinition, MPPartitioner, MPTableAsset
from great_expectations.datasource.fluent.batch_request import BatchParameters


SnowflakeAssetPartitionerT = TypeVar("SnowflakeAssetPartitionerT", bound=Union["SnowflakeTableAssetColumnYearlyPartitioner", "SnowflakeTableAssetColumnDailyPartitioner"])

class SnowflakeTableAssetColumnYearlyPartitioner(MPPartitioner):
    column: str

    def get_where_clause_str(self, batch_parameters: BatchParameters) -> str:
        return f"YEAR({self.column}) = {batch_parameters['year']}"


class SnowflakeTableAssetColumnDailyPartitioner(MPPartitioner):
    column: str

    def get_where_clause_str(self, batch_parameters: BatchParameters) -> str:
        return f"YEAR({self.column}) = {batch_parameters['year']} AND MONTH({self.column}) = {batch_parameters['month']} AND DAY({self.column}) = {batch_parameters['day']}"



class SnowflakeMPBatchDefinition(MPBatchDefinition[MPTableAsset, SnowflakeAssetPartitionerT]):
    def get_selectable_str(self, batch_parameters: BatchParameters) -> str:
        return f"{self.data_asset.table_name} WHERE {self.partitioner.get_where_clause_str(batch_parameters=batch_parameters)}"

