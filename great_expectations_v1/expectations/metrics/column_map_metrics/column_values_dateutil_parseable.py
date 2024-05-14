from __future__ import annotations

from dateutil.parser import parse

from great_expectations_v1.execution_engine import PandasExecutionEngine
from great_expectations_v1.expectations.metrics.map_metric_provider import (
    ColumnMapMetricProvider,
    column_condition_partial,
)


class ColumnValuesDateutilParseable(ColumnMapMetricProvider):
    condition_metric_name = "column_values.dateutil_parseable"

    @column_condition_partial(engine=PandasExecutionEngine)
    def _pandas(cls, column, **kwargs):
        def is_parseable(val):
            try:
                if type(val) != str:  # noqa: E721
                    raise TypeError(  # noqa: TRY003
                        "Values passed to expect_column_values_to_be_dateutil_parseable must be of type string.\nIf you want to validate a column of dates or timestamps, please call the expectation before converting from string format."  # noqa: E501
                    )

                parse(val)
                return True

            except (ValueError, OverflowError):
                return False

        return column.map(is_parseable)