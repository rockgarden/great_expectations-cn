from __future__ import annotations

import datetime
import json
import logging
from typing import TYPE_CHECKING, Any

import great_expectations_v1.exceptions as gx_exceptions
from great_expectations_v1.compatibility.typing_extensions import override
from great_expectations_v1.core.util import datetime_to_int, parse_string_to_datetime
from great_expectations_v1.datasource.data_connector.sorter import Sorter

if TYPE_CHECKING:
    from great_expectations_v1.core.batch import LegacyBatchDefinition

logger = logging.getLogger(__name__)


class DateTimeSorter(Sorter):
    def __init__(self, name: str, orderby: str = "asc", datetime_format="%Y%m%d") -> None:
        super().__init__(name=name, orderby=orderby)

        if datetime_format and not isinstance(datetime_format, str):
            raise gx_exceptions.SorterError(  # noqa: TRY003
                f"""DateTime parsing formatter "datetime_format_string" must have string type (actual type is
        "{type(datetime_format)!s}").
                    """  # noqa: E501
            )

        self._datetime_format = datetime_format

    @override
    def get_batch_key(self, batch_definition: LegacyBatchDefinition) -> Any:
        batch_identifiers: dict = batch_definition.batch_identifiers
        partition_value: Any = batch_identifiers[self.name]
        dt: datetime.date = parse_string_to_datetime(
            datetime_string=partition_value,
            datetime_format_string=self._datetime_format,
        )
        return datetime_to_int(dt=dt)

    @override
    def __repr__(self) -> str:
        doc_fields_dict: dict = {
            "name": self.name,
            "reverse": self.reverse,
            "type": "DateTimeSorter",
            "date_time_format": self._datetime_format,
        }
        return json.dumps(doc_fields_dict, indent=2)