"""
An action is a way to take an arbitrary method and make it configurable and runnable within a Data Context.

The only requirement from an action is for it to have a take_action method.
"""  # noqa: E501

from __future__ import annotations

import json
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Union,
)

import requests

from great_expectations._docs_decorators import public_api
from great_expectations.compatibility import aws
from great_expectations.compatibility.pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    root_validator,
    validator,
)
from great_expectations.compatibility.pypd import pypd
from great_expectations.compatibility.typing_extensions import override
from great_expectations.core.util import convert_to_json_serializable
from great_expectations.data_context.cloud_constants import GXCloudRESTResource
from great_expectations.data_context.store.validations_store import ValidationsStore
from great_expectations.data_context.types.refs import GXCloudResourceRef
from great_expectations.data_context.types.resource_identifiers import (
    ExpectationSuiteIdentifier,
    GXCloudIdentifier,
    ValidationResultIdentifier,
)
from great_expectations.data_context.util import instantiate_class_from_config
from great_expectations.exceptions import ClassInstantiationError
from great_expectations.render.renderer import (
    EmailRenderer,
    MicrosoftTeamsRenderer,
    OpsgenieRenderer,
    SlackRenderer,
)
from great_expectations.render.renderer.renderer import Renderer

if TYPE_CHECKING:
    from great_expectations.checkpoint.v1_checkpoint import CheckpointResult
    from great_expectations.core.expectation_validation_result import (
        ExpectationSuiteValidationResult,
    )
    from great_expectations.data_context import AbstractDataContext

logger = logging.getLogger(__name__)


def _build_renderer(config: dict) -> Renderer:
    renderer = instantiate_class_from_config(
        config=config,
        runtime_environment={},
        config_defaults={"module_name": "great_expectations.render.renderer"},
    )
    if not renderer:
        raise ClassInstantiationError(
            module_name=config.get("module_name"),
            package_name=None,
            class_name=config.get("class_name"),
        )
    return renderer


@public_api
class ValidationAction(BaseModel):
    """
    ValidationActions define a set of steps to be run after a validation result is produced.

    Through a Checkpoint, one can orchestrate the validation of data and configure notifications, data documentation updates,
    and other actions to take place after the validation result is produced.
    """  # noqa: E501

    class Config:
        arbitrary_types_allowed = True
        # Due to legacy pattern of instantiate_class_from_config, we need a custom serializer
        json_encoders = {Renderer: lambda r: r.serialize()}

    type: str
    notify_on: Literal["all", "failure", "success"] = "all"

    @property
    def _using_cloud_context(self) -> bool:
        from great_expectations import project_manager

        return project_manager.is_using_cloud()

    @public_api
    def run(
        self,
        validation_result_suite: ExpectationSuiteValidationResult,
        validation_result_suite_identifier: Union[ValidationResultIdentifier, GXCloudIdentifier],
        expectation_suite_identifier: Optional[ExpectationSuiteIdentifier] = None,
        checkpoint_identifier=None,
        **kwargs,
    ):
        """Public entrypoint GX uses to trigger a ValidationAction.

        When a ValidationAction is configured in a Checkpoint, this method gets called
        after the Checkpoint produces an ExpectationSuiteValidationResult.

        Args:
            validation_result_suite: An instance of the ExpectationSuiteValidationResult class.
            validation_result_suite_identifier: an instance of either the ValidationResultIdentifier class (for open source Great Expectations) or the GXCloudIdentifier (from Great Expectations Cloud).
            expectation_suite_identifier: Optionally, an instance of the ExpectationSuiteIdentifier class.
            checkpoint_identifier: Optionally, an Identifier for the Checkpoint.
            kwargs: named parameters that are specific to a given Action, and need to be assigned a value in the Action's configuration in a Checkpoint's action_list.

        Returns:
            A Dict describing the result of the Action.
        """  # noqa: E501
        return self._run(
            validation_result_suite=validation_result_suite,
            validation_result_suite_identifier=validation_result_suite_identifier,
            expectation_suite_identifier=expectation_suite_identifier,
            checkpoint_identifier=checkpoint_identifier,
            **kwargs,
        )

    def _run(
        self,
        validation_result_suite: ExpectationSuiteValidationResult,
        validation_result_suite_identifier: Union[ValidationResultIdentifier, GXCloudIdentifier],
        expectation_suite_identifier=None,
        checkpoint_identifier=None,
    ):
        """Private method containing the logic specific to a ValidationAction's implementation.

        The implementation details specific to this ValidationAction must live in this method.
        Additional context required by the ValidationAction may be specified in the Checkpoint's
        `action_list` under the `action` key. These arbitrary key/value pairs will be passed into
        the ValidationAction as keyword arguments.

        Args:
            validation_result_suite: An instance of the ExpectationSuiteValidationResult class.
            validation_result_suite_identifier: an instance of either the ValidationResultIdentifier
                class (for open source Great Expectations) or the GeCloudIdentifier (from Great Expectations Cloud).
            expectation_suite_identifier:  Optionally, an instance of the ExpectationSuiteIdentifier class.
            checkpoint_identifier:  Optionally, an Identifier for the Checkpoints.

        Returns:
            A Dict describing the result of the Action.
        """  # noqa: E501

    # NOTE: To be promoted to 'run' after V1 development (JIRA: V1-271)
    def v1_run(self, checkpoint_result: CheckpointResult) -> str | dict:
        raise NotImplementedError

    def _is_enabled(self, success: bool) -> bool:
        return (
            self.notify_on == "all"
            or self.notify_on == "success"
            and success
            or self.notify_on == "failure"
            and not success
        )


class DataDocsAction(ValidationAction):
    def _build_data_docs(
        self,
        site_names: list[str] | None = None,
        resource_identifiers: list | None = None,
    ) -> dict:
        from great_expectations import project_manager

        return project_manager.build_data_docs(
            site_names=site_names, resource_identifiers=resource_identifiers
        )

    def _get_docs_sites_urls(
        self,
        site_names: list[str] | None = None,
        resource_identifier: Any | None = None,
    ):
        from great_expectations import project_manager

        return project_manager.get_docs_sites_urls(
            site_names=site_names, resource_identifier=resource_identifier
        )


@public_api
class SlackNotificationAction(DataDocsAction):
    """Sends a Slack notification to a given webhook.

    ```yaml
    - name: send_slack_notification_on_validation_result
    action:
      class_name: SlackNotificationAction
      # put the actual webhook URL in the uncommitted/config_variables.yml file
      # or pass in as environment variable
      # use slack_webhook when not using slack bot token
      slack_webhook: ${validation_notification_slack_webhook}
      slack_token:
      slack_channel:
      notify_on: all
      notify_with:
      renderer:
        # the class that implements the message to be sent
        # this is the default implementation, but you can
        # implement a custom one
        module_name: great_expectations.render.renderer.slack_renderer
        class_name: SlackRenderer
      show_failed_expectations: True
    ```

    Args:
        renderer: Specifies the Renderer used to generate a query consumable by Slack API, e.g.:
            ```python
            {
               "module_name": "great_expectations.render.renderer.slack_renderer",
               "class_name": "SlackRenderer",
           }
           ```
        slack_webhook: The incoming Slack webhook to which to send notification.
        slack_token: Token from Slack app. Used when not using slack_webhook.
        slack_channel: Slack channel to receive notification. Used when not using slack_webhook.
        notify_on: Specifies validation status that triggers notification. One of "all", "failure", "success".
        notify_with: List of DataDocs site names to display  in Slack messages. Defaults to all.
        show_failed_expectations: Shows a list of failed expectation types.
    """  # noqa: E501

    type: Literal["slack"] = "slack"

    slack_webhook: Optional[str] = None
    slack_token: Optional[str] = None
    slack_channel: Optional[str] = None
    notify_on: Literal["all", "failure", "success"] = "all"
    notify_with: Optional[List[str]] = None
    show_failed_expectations: bool = False
    renderer: SlackRenderer = Field(default_factory=SlackRenderer)

    @validator("renderer", pre=True)
    def _validate_renderer(cls, renderer: dict | SlackRenderer) -> SlackRenderer:
        if isinstance(renderer, dict):
            _renderer = _build_renderer(config=renderer)
            if not isinstance(_renderer, SlackRenderer):
                raise ValueError(  # noqa: TRY003, TRY004
                    "renderer must be a SlackRenderer or a valid configuration for one."
                )
            renderer = _renderer
        return renderer

    @root_validator
    def _root_validate_slack_params(cls, values: dict) -> dict:
        slack_token = values["slack_token"]
        slack_channel = values["slack_channel"]
        slack_webhook = values["slack_webhook"]

        try:
            if slack_webhook:
                assert not slack_token and not slack_channel
            else:
                assert slack_token and slack_channel
        except AssertionError:
            raise ValueError("Please provide either slack_webhook or slack_token and slack_channel")  # noqa: TRY003

        return values

    @override
    def _run(  # type: ignore[override] # signature does not match parent  # noqa: C901, PLR0913
        self,
        validation_result_suite: ExpectationSuiteValidationResult,
        validation_result_suite_identifier: Union[ValidationResultIdentifier, GXCloudIdentifier],
        payload=None,
        expectation_suite_identifier=None,
        checkpoint_identifier=None,
    ):
        logger.debug("SlackNotificationAction.run")

        if validation_result_suite is None:
            logger.warning(
                f"No validation_result_suite was passed to {type(self).__name__} action. Skipping action."  # noqa: E501
            )
            return

        if not isinstance(
            validation_result_suite_identifier,
            (ValidationResultIdentifier, GXCloudIdentifier),
        ):
            raise TypeError(  # noqa: TRY003
                "validation_result_suite_id must be of type ValidationResultIdentifier or GeCloudIdentifier, "  # noqa: E501
                f"not {type(validation_result_suite_identifier)}"
            )

        validation_success = validation_result_suite.success
        data_docs_pages = None
        if payload:
            # process the payload
            for action_names in payload.keys():
                if payload[action_names]["class"] == "UpdateDataDocsAction":
                    data_docs_pages = payload[action_names]

        # Assemble complete GX Cloud URL for a specific validation result
        data_docs_urls: list[dict[str, str]] = self._get_docs_sites_urls(
            resource_identifier=validation_result_suite_identifier
        )

        validation_result_urls: list[str] = [
            data_docs_url["site_url"]
            for data_docs_url in data_docs_urls
            if data_docs_url["site_url"]
        ]
        if (
            isinstance(validation_result_suite_identifier, GXCloudIdentifier)
            and validation_result_suite_identifier.id
        ):
            # To send a notification with a link to the validation result, we need to have created the validation  # noqa: E501
            # result in cloud. If the user has configured the store action after the notification action, they will  # noqa: E501
            # get a warning that no link will be provided. See the __init__ method for ActionListValidationOperator.  # noqa: E501
            if (
                "store_validation_result" in payload
                and "validation_result_url" in payload["store_validation_result"]
            ):
                validation_result_urls.append(
                    payload["store_validation_result"]["validation_result_url"]
                )
        result = {"slack_notification_result": "none required"}
        if self._is_enabled(success=validation_success):
            query: Dict = self.renderer.render(
                validation_result_suite,
                data_docs_pages,
                self.notify_with,
                self.show_failed_expectations,
                validation_result_urls,
            )

            blocks = query.get("blocks")
            if blocks:
                if len(blocks) >= 1:
                    if blocks[0].get("text"):
                        result = self._send_notifications_in_batches(blocks, query, result)
                    else:
                        result = self._get_slack_result(query)

        return result

    def _send_notifications_in_batches(self, blocks, query, result):
        text = blocks[0]["text"]["text"]
        chunks, chunk_size = len(text), len(text) // 4
        split_text = [
            text[position : position + chunk_size] for position in range(0, chunks, chunk_size)
        ]
        for batch in split_text:
            query["text"] = batch
            result = self._get_slack_result(query)
        return result

    def _get_slack_result(self, query):
        # this will actually send the POST request to the Slack webapp server
        slack_notif_result = self._send_slack_notification(
            query,
            slack_webhook=self.slack_webhook,
            slack_token=self.slack_token,
            slack_channel=self.slack_channel,
        )
        return {"slack_notification_result": slack_notif_result}

    @staticmethod
    def _send_slack_notification(query, slack_webhook=None, slack_channel=None, slack_token=None):
        session = requests.Session()
        url = slack_webhook
        headers = None

        # Slack doc about overwritting the channel when using the legacy Incoming Webhooks
        # https://api.slack.com/legacy/custom-integrations/messaging/webhooks
        # ** Since it is legacy, it could be deprecated or removed in the future **
        if slack_channel:
            query["channel"] = slack_channel

        if not slack_webhook:
            url = "https://slack.com/api/chat.postMessage"
            headers = {"Authorization": f"Bearer {slack_token}"}

        try:
            response = session.post(url=url, headers=headers, json=query)
            if slack_webhook:
                ok_status = response.text == "ok"
            else:
                ok_status = response.json()["ok"]
        except requests.ConnectionError:
            logger.warning(f"Failed to connect to Slack webhook after {10} retries.")
        except Exception as e:
            logger.error(str(e))  # noqa: TRY400
        else:
            if response.status_code != 200 or not ok_status:  # noqa: PLR2004
                logger.warning(
                    "Request to Slack webhook "
                    f"returned error {response.status_code}: {response.text}"
                )

            else:
                return "Slack notification succeeded."


@public_api
class PagerdutyAlertAction(ValidationAction):
    """Sends a PagerDuty event.

    ```yaml
    - name: send_pagerduty_alert_on_validation_result
    action:
      class_name: PagerdutyAlertAction
      api_key: ${pagerduty_api_key}
      routing_key: ${pagerduty_routing_key}
      notify_on: failure
      severity: critical
    ```

    Args:
        api_key: Events API v2 key for pagerduty.
        routing_key: The 32 character Integration Key for an integration on a service or on a global ruleset.
        notify_on: Specifies validation status that triggers notification. One of "all", "failure", "success".
        severity: The PagerDuty severity levels determine the level of urgency. One of "critical", "error", "warning", or "info".
    """  # noqa: E501

    type: Literal["pagerduty"] = "pagerduty"

    api_key: str
    routing_key: str
    notify_on: Literal["all", "failure", "success"] = "failure"
    severity: Literal["critical", "error", "warning", "info"] = "critical"

    @override
    def v1_run(self, checkpoint_result: CheckpointResult) -> dict:
        success = checkpoint_result.success or False
        checkpoint_name = checkpoint_result.checkpoint_config.name
        summary = f"Great Expectations Checkpoint {checkpoint_name} has "
        if success:
            summary += "succeeded"
        else:
            summary += "failed"

        return self._run_pypd_alert(dedup_key=checkpoint_name, message=summary, success=success)

    @override
    def _run(  # type: ignore[override] # signature does not match parent  # noqa: PLR0913
        self,
        validation_result_suite: ExpectationSuiteValidationResult,
        validation_result_suite_identifier: Union[ValidationResultIdentifier, GXCloudIdentifier],
        payload=None,
        expectation_suite_identifier=None,
        checkpoint_identifier=None,
    ):
        logger.debug("PagerdutyAlertAction.run")

        if validation_result_suite is None:
            logger.warning(
                f"No validation_result_suite was passed to {type(self).__name__} action. Skipping action."  # noqa: E501
            )
            return

        if not isinstance(
            validation_result_suite_identifier,
            (ValidationResultIdentifier, GXCloudIdentifier),
        ):
            raise TypeError(  # noqa: TRY003
                "validation_result_suite_id must be of type ValidationResultIdentifier or GeCloudIdentifier, "  # noqa: E501
                f"not {type(validation_result_suite_identifier)}"
            )

        validation_success = validation_result_suite.success
        expectation_suite_name = validation_result_suite.meta.get(
            "expectation_suite_name", "__no_expectation_suite_name__"
        )

        return self._run_pypd_alert(
            dedup_key=expectation_suite_name,
            message=f"Great Expectations suite check {expectation_suite_name} has failed",
            success=validation_success,
        )

    def _run_pypd_alert(self, dedup_key: str, message: str, success: bool):
        if self._is_enabled(success=success):
            pypd.api_key = self.api_key
            pypd.EventV2.create(
                data={
                    "routing_key": self.routing_key,
                    "dedup_key": dedup_key,
                    "event_action": "trigger",
                    "payload": {
                        "summary": message,
                        "severity": self.severity,
                        "source": "Great Expectations",
                    },
                }
            )

            return {"pagerduty_alert_result": "success"}

        return {"pagerduty_alert_result": "none sent"}


@public_api
class MicrosoftTeamsNotificationAction(ValidationAction):
    """Sends a Microsoft Teams notification to a given webhook.

    ```yaml
    - name: send_microsoft_teams_notification_on_validation_result
    action:
      class_name: MicrosoftTeamsNotificationAction
      # put the actual webhook URL in the uncommitted/config_variables.yml file
      # or pass in as environment variable
      microsoft_teams_webhook: ${validation_notification_microsoft_teams_webhook}
      notify_on: all
      renderer:
        # the class that implements the message to be sent
        # this is the default implementation, but you can
        # implement a custom one
        module_name: great_expectations.render.renderer.microsoft_teams_renderer
        class_name: MicrosoftTeamsRenderer
    ```

    Args:
        renderer: Specifies the renderer used to generate a query consumable by teams API, e.g.:
            ```python
            {
               "module_name": "great_expectations.render.renderer.microsoft_teams_renderer",
               "class_name": "MicrosoftTeamsRenderer",
            }
            ```
        microsoft_teams_webhook: Incoming Microsoft Teams webhook to which to send notifications.
        notify_on: Specifies validation status that triggers notification. One of "all", "failure", "success".
    """  # noqa: E501

    type: Literal["microsoft"] = "microsoft"

    teams_webhook: str
    notify_on: Literal["all", "failure", "success"] = "all"
    renderer: MicrosoftTeamsRenderer = Field(default_factory=MicrosoftTeamsRenderer)

    @validator("renderer", pre=True)
    def _validate_renderer(cls, renderer: dict | MicrosoftTeamsRenderer) -> MicrosoftTeamsRenderer:
        if isinstance(renderer, dict):
            _renderer = _build_renderer(config=renderer)
            if not isinstance(_renderer, MicrosoftTeamsRenderer):
                raise ValueError(  # noqa: TRY003, TRY004
                    "renderer must be a MicrosoftTeamsRenderer or a valid configuration for one."
                )
            renderer = _renderer
        return renderer

    @override
    def _run(  # type: ignore[override] # signature does not match parent  # noqa: PLR0913
        self,
        validation_result_suite: ExpectationSuiteValidationResult,
        validation_result_suite_identifier: Union[ValidationResultIdentifier, GXCloudIdentifier],
        payload=None,
        expectation_suite_identifier=None,
        checkpoint_identifier=None,
    ):
        logger.debug("MicrosoftTeamsNotificationAction.run")

        if validation_result_suite is None:
            logger.warning(
                f"No validation_result_suite was passed to {type(self).__name__} action. Skipping action."  # noqa: E501
            )
            return

        if not isinstance(
            validation_result_suite_identifier,
            (ValidationResultIdentifier, GXCloudIdentifier),
        ):
            raise TypeError(  # noqa: TRY003
                "validation_result_suite_id must be of type ValidationResultIdentifier or GeCloudIdentifier, "  # noqa: E501
                f"not {type(validation_result_suite_identifier)}"
            )
        validation_success = validation_result_suite.success
        data_docs_pages = None

        if payload:
            # process the payload
            for action_names in payload.keys():
                if payload[action_names]["class"] == "UpdateDataDocsAction":
                    data_docs_pages = payload[action_names]

        if self._is_enabled(success=validation_success):
            query = self.renderer.render(
                validation_result_suite,
                validation_result_suite_identifier,
                data_docs_pages,
            )
            # this will actually sent the POST request to the Microsoft Teams webapp server
            teams_notif_result = self._send_microsoft_teams_notifications(
                query, microsoft_teams_webhook=self.teams_webhook
            )
            return {"microsoft_teams_notification_result": teams_notif_result}
        else:
            return {"microsoft_teams_notification_result": None}

    @staticmethod
    def _send_microsoft_teams_notifications(query: str, microsoft_teams_webhook: str) -> str | None:
        session = requests.Session()
        try:
            response = session.post(url=microsoft_teams_webhook, json=query)
        except requests.ConnectionError:
            logger.warning("Failed to connect to Microsoft Teams webhook after 10 retries.")

        except Exception as e:
            logger.error(str(e))  # noqa: TRY400
        else:
            if response.status_code != 200:  # noqa: PLR2004
                logger.warning(
                    "Request to Microsoft Teams webhook "
                    f"returned error {response.status_code}: {response.text}"
                )
                return
            else:
                return "Microsoft Teams notification succeeded."


@public_api
class OpsgenieAlertAction(ValidationAction):
    """Sends an Opsgenie alert.

    ```yaml
    - name: send_opsgenie_alert_on_validation_result
    action:
      class_name: OpsgenieAlertAction
      # put the actual webhook URL in the uncommitted/config_variables.yml file
      # or pass in as environment variable
      api_key: ${opsgenie_api_key}
      region:
      priority: P2
      notify_on: failure
    ```

    Args:
        api_key: Opsgenie API key.
        region: Specifies the Opsgenie region. Populate 'EU' for Europe otherwise do not set.
        priority: Specifies the priority of the alert (P1 - P5).
        notify_on: Specifies validation status that triggers notification. One of "all", "failure", "success".
        tags: Tags to include in the alert
    """  # noqa: E501

    type: Literal["opsgenie"] = "opsgenie"

    api_key: str
    region: Optional[str] = None
    priority: Literal["P1", "P2", "P3", "P4", "P5"] = "P3"
    notify_on: Literal["all", "failure", "success"] = "failure"
    tags: Optional[List[str]] = None
    renderer: OpsgenieRenderer = Field(default_factory=OpsgenieRenderer)

    @validator("renderer", pre=True)
    def _validate_renderer(cls, renderer: dict | OpsgenieRenderer) -> OpsgenieRenderer:
        if isinstance(renderer, dict):
            _renderer = _build_renderer(config=renderer)
            if not isinstance(_renderer, OpsgenieRenderer):
                raise ValueError(  # noqa: TRY003, TRY004
                    "renderer must be a OpsgenieRenderer or a valid configuration for one."
                )
            renderer = _renderer
        return renderer

    @override
    def v1_run(self, checkpoint_result: CheckpointResult) -> dict:
        validation_success = checkpoint_result.success or False
        checkpoint_name = checkpoint_result.checkpoint_config.name

        if self._is_enabled(success=validation_success):
            settings = {
                "api_key": self.api_key,
                "region": self.region,
                "priority": self.priority,
                "tags": self.tags,
            }

            description = self.renderer.v1_render(checkpoint_result=checkpoint_result)

            message = f"Great Expectations Checkpoint {checkpoint_name} "
            if checkpoint_result.success:
                message += "succeeded!"
            else:
                message += "failed!"

            alert_result = self._send_opsgenie_alert(
                query=description, message=message, settings=settings
            )

            return {"opsgenie_alert_result": alert_result}
        else:
            return {"opsgenie_alert_result": "No alert sent"}

    @override
    def _run(  # type: ignore[override] # signature does not match parent  # noqa: PLR0913
        self,
        validation_result_suite: ExpectationSuiteValidationResult,
        validation_result_suite_identifier: Union[ValidationResultIdentifier, GXCloudIdentifier],
        payload=None,
        expectation_suite_identifier=None,
        checkpoint_identifier=None,
    ):
        logger.debug("OpsgenieAlertAction.run")

        if validation_result_suite is None:
            logger.warning(
                f"No validation_result_suite was passed to {type(self).__name__} action. Skipping action."  # noqa: E501
            )
            return

        if not isinstance(
            validation_result_suite_identifier,
            (ValidationResultIdentifier, GXCloudIdentifier),
        ):
            raise TypeError(  # noqa: TRY003
                "validation_result_suite_id must be of type ValidationResultIdentifier or GeCloudIdentifier, "  # noqa: E501
                f"not {type(validation_result_suite_identifier)}"
            )

        validation_success = validation_result_suite.success

        if self._is_enabled(success=validation_success):
            expectation_suite_name = validation_result_suite.meta.get(
                "expectation_suite_name", "__no_expectation_suite_name__"
            )

            settings = {
                "api_key": self.api_key,
                "region": self.region,
                "priority": self.priority,
                "tags": self.tags,
            }

            description = self.renderer.render(validation_result_suite, None, None)

            message = f"Great Expectations suite {expectation_suite_name} failed"
            alert_result = self._send_opsgenie_alert(description, message, settings)

            return {"opsgenie_alert_result": alert_result}
        else:
            return {"opsgenie_alert_result": ""}

    @staticmethod
    def _send_opsgenie_alert(query: str, message: str, settings: dict) -> bool:
        """Creates an alert in Opsgenie."""
        if settings["region"] is not None:
            # accommodate for Europeans
            url = f"https://api.{settings['region']}.opsgenie.com/v2/alerts"
        else:
            url = "https://api.opsgenie.com/v2/alerts"

        headers = {"Authorization": f"GenieKey {settings['api_key']}"}
        payload = {
            "message": message,
            "description": query,
            "priority": settings["priority"],  # allow this to be modified in settings
            "tags": settings["tags"],
        }

        session = requests.Session()

        try:
            response = session.post(url, headers=headers, json=payload)
            response.raise_for_status()
        except requests.ConnectionError as e:
            logger.warning(f"Failed to connect to Opsgenie: {e}")
            return False
        except requests.HTTPError as e:
            logger.warning(f"Request to Opsgenie API returned error {response.status_code}: {e}")
            return False
        return True


@public_api
class EmailAction(ValidationAction):
    """Sends an email to a given list of email addresses.

    ```yaml
    - name: send_email_on_validation_result
    action:
      class_name: EmailAction
      notify_on: all # possible values: "all", "failure", "success"
      notify_with:
      renderer:
        # the class that implements the message to be sent
        # this is the default implementation, but you can
        # implement a custom one
        module_name: great_expectations.render.renderer.email_renderer
        class_name: EmailRenderer
      # put the actual following information in the uncommitted/config_variables.yml file
      # or pass in as environment variable
      smtp_address: ${smtp_address}
      smtp_port: ${smtp_port}
      sender_login: ${email_address}
      sender_password: ${sender_password}
      sender_alias: ${sender_alias} # useful to send an email as an alias
      receiver_emails: ${receiver_emails}
      use_tls: False
      use_ssl: True
    ```

    Args:
        renderer: Specifies the renderer used to generate an email, for example:
            ```python
            {
               "module_name": "great_expectations.render.renderer.email_renderer",
               "class_name": "EmailRenderer",
            }
            ```
        smtp_address: Address of the SMTP server used to send the email.
        smtp_address: Port of the SMTP server used to send the email.
        sender_login: Login used send the email.
        sender_password: Password used to send the email.
        sender_alias: Optional. Alias used to send the email (default = sender_login).
        receiver_emails: Email addresses that will receive the email (separated by commas).
        use_tls: Optional. Use of TLS to send the email (using either TLS or SSL is highly recommended).
        use_ssl: Optional. Use of SSL to send the email (using either TLS or SSL is highly recommended).
        notify_on: "Specifies validation status that triggers notification. One of "all", "failure", "success".
        notify_with: Optional list of DataDocs site names to display  in Slack messages. Defaults to all.
    """  # noqa: E501

    type: Literal["email"] = "email"

    smtp_address: str
    smtp_port: str
    receiver_emails: str
    sender_login: Optional[str] = None
    sender_password: Optional[str] = None
    sender_alias: Optional[str] = None
    use_tls: Optional[bool] = None
    use_ssl: Optional[bool] = None
    notify_on: Literal["all", "failure", "success"] = "all"
    notify_with: Optional[List[str]] = None
    renderer: EmailRenderer = Field(default_factory=EmailRenderer)

    @validator("renderer", pre=True)
    def _validate_renderer(cls, renderer: dict | EmailRenderer) -> EmailRenderer:
        if isinstance(renderer, dict):
            _renderer = _build_renderer(config=renderer)
            if not isinstance(_renderer, EmailRenderer):
                raise ValueError(  # noqa: TRY003, TRY004
                    "renderer must be a EmailRenderer or a valid configuration for one."
                )
            renderer = _renderer
        return renderer

    @root_validator
    def _root_validate_email_params(cls, values: dict) -> dict:
        if not values["sender_alias"]:
            values["sender_alias"] = values["sender_login"]

        if not values["sender_login"]:
            logger.warning(
                "No login found for sending the email in action config. "
                "This will only work for email server that does not require authentication."
            )
        if not values["sender_password"]:
            logger.warning(
                "No password found for sending the email in action config."
                "This will only work for email server that does not require authentication."
            )

        return values

    @override
    def _run(  # type: ignore[override] # signature does not match parent  # noqa: PLR0913
        self,
        validation_result_suite: ExpectationSuiteValidationResult,
        validation_result_suite_identifier: Union[ValidationResultIdentifier, GXCloudIdentifier],
        payload=None,
        expectation_suite_identifier=None,
        checkpoint_identifier=None,
    ):
        logger.debug("EmailAction.run")

        if validation_result_suite is None:
            logger.warning(
                f"No validation_result_suite was passed to {type(self).__name__} action. Skipping action."  # noqa: E501
            )
            return

        if not isinstance(
            validation_result_suite_identifier,
            (ValidationResultIdentifier, GXCloudIdentifier),
        ):
            raise TypeError(  # noqa: TRY003
                "validation_result_suite_id must be of type ValidationResultIdentifier or GeCloudIdentifier, "  # noqa: E501
                f"not {type(validation_result_suite_identifier)}"
            )

        validation_success = validation_result_suite.success
        data_docs_pages = None

        if payload:
            # process the payload
            for action_names in payload.keys():
                if payload[action_names]["class"] == "UpdateDataDocsAction":
                    data_docs_pages = payload[action_names]

        if self._is_enabled(success=validation_success):
            title, html = self.renderer.render(
                validation_result_suite, data_docs_pages, self.notify_with
            )

            receiver_emails_list = list(map(lambda x: x.strip(), self.receiver_emails.split(",")))

            # this will actually send the email
            email_result = self._send_email(
                title,
                html,
                self.smtp_address,
                self.smtp_port,
                self.sender_login,
                self.sender_password,
                self.sender_alias,
                receiver_emails_list,
                self.use_tls,
                self.use_ssl,
            )

            # sending payload back as dictionary
            return {"email_result": email_result}
        else:
            return {"email_result": ""}

    @staticmethod
    def _send_email(  # noqa: C901, PLR0913
        title: str,
        html: str,
        smtp_address: str,
        smtp_port: str,
        sender_login: str | None,
        sender_password: str | None,
        sender_alias: str | None,
        receiver_emails_list: list[str],
        use_tls: bool | None,
        use_ssl: bool | None,
    ) -> str:
        msg = MIMEMultipart()
        msg["From"] = sender_alias
        msg["To"] = ", ".join(receiver_emails_list)
        msg["Subject"] = title
        msg.attach(MIMEText(html, "html"))
        try:
            if use_ssl:
                if use_tls:
                    logger.warning("Please choose between SSL or TLS, will default to SSL")
                context = ssl.create_default_context()
                mailserver = smtplib.SMTP_SSL(smtp_address, smtp_port, context=context)
            elif use_tls:
                mailserver = smtplib.SMTP(smtp_address, smtp_port)
                context = ssl.create_default_context()
                mailserver.starttls(context=context)
            else:
                logger.warning("Not using TLS or SSL to send an email is not secure")
                mailserver = smtplib.SMTP(smtp_address, smtp_port)
            if sender_login is not None and sender_password is not None:
                mailserver.login(sender_login, sender_password)
            elif not (sender_login is None and sender_password is None):
                logger.error(
                    "Please specify both sender_login and sender_password or specify both as None"
                )
            mailserver.sendmail(sender_alias, receiver_emails_list, msg.as_string())
            mailserver.quit()
        except smtplib.SMTPConnectError:
            logger.error(f"Failed to connect to the SMTP server at address: {smtp_address}")  # noqa: TRY400
        except smtplib.SMTPAuthenticationError:
            logger.error(f"Failed to authenticate to the SMTP server at address: {smtp_address}")  # noqa: TRY400
        except Exception as e:
            logger.error(str(e))  # noqa: TRY400
        else:
            return "success"


# TODO: This action is slated for deletion in favor of using ValidationResult.run()
@public_api
class StoreValidationResultAction(ValidationAction):
    """Store a validation result in the ValidationsStore.
    Typical usage example:
        ```yaml
        - name: store_validation_result
        action:
          class_name: StoreValidationResultAction
          # name of the store where the actions will store validation results
          # the name must refer to a store that is configured in the great_expectations.yml file
          target_store_name: validations_store
        ```
    Args:
        data_context: GX Data Context.
        target_store_name: The name of the store where the actions will store the validation result.
    Raises:
        TypeError: validation_result_id must be of type ValidationResultIdentifier or GeCloudIdentifier, not {}.
    """  # noqa: E501

    type: Literal["store_validation_result"] = "store_validation_result"

    class Config:
        arbitrary_types_allowed = True

    _target_store: ValidationsStore = PrivateAttr()

    def __init__(
        self,
        data_context: AbstractDataContext,
        target_store_name: Optional[str] = None,
    ) -> None:
        super().__init__(type="store_validation_result")
        if target_store_name is None:
            target_store = data_context.stores[data_context.validations_store_name]
        else:
            target_store = data_context.stores[target_store_name]

        if not isinstance(target_store, ValidationsStore):
            raise ValueError("target_store must be a ValidationsStore")  # noqa: TRY003, TRY004

        self._target_store = target_store

    @override
    def _run(  # type: ignore[override] # signature does not match parent  # noqa: PLR0913
        self,
        validation_result_suite: ExpectationSuiteValidationResult,
        validation_result_suite_identifier: Union[ValidationResultIdentifier, GXCloudIdentifier],
        payload=None,
        expectation_suite_identifier=None,
        checkpoint_identifier: Optional[GXCloudIdentifier] = None,
    ):
        logger.debug("StoreValidationResultAction.run")

        output = self._target_store.store_validation_results(
            validation_result_suite,
            validation_result_suite_identifier,
            expectation_suite_identifier,
            checkpoint_identifier,
        )

        if isinstance(output, GXCloudResourceRef) and isinstance(
            validation_result_suite_identifier, GXCloudIdentifier
        ):
            validation_result_suite_identifier.id = output.id

        if self._using_cloud_context and isinstance(output, GXCloudResourceRef):
            return output


@public_api
class UpdateDataDocsAction(DataDocsAction):
    """Notify the site builders of all data docs sites of a Data Context that a validation result should be added to the data docs.

    YAML configuration example:

    ```yaml
    - name: update_data_docs
    action:
      class_name: UpdateDataDocsAction
    ```

    You can also instruct ``UpdateDataDocsAction`` to build only certain sites by providing a ``site_names`` key with a
    list of sites to update:

    ```yaml
    - name: update_data_docs
    action:
      class_name: UpdateDataDocsAction
      site_names:
        - local_site
    ```

    Args:
        site_names: Optional. A list of the names of sites to update.
    """  # noqa: E501

    type: Literal["update_data_docs"] = "update_data_docs"

    site_names: List[str] = []

    @override
    def v1_run(self, checkpoint_result: CheckpointResult) -> dict:
        action_results: dict[ValidationResultIdentifier, dict] = {}
        for result_identifier, result in checkpoint_result.run_results.items():
            suite_name = result.suite_name

            expectation_suite_identifier: ExpectationSuiteIdentifier | GXCloudIdentifier
            if self._using_cloud_context:
                expectation_suite_identifier = GXCloudIdentifier(
                    resource_type=GXCloudRESTResource.EXPECTATION_SUITE, resource_name=suite_name
                )
            else:
                expectation_suite_identifier = ExpectationSuiteIdentifier(name=suite_name)

            action_result = self._run(
                validation_result_suite=result,
                validation_result_suite_identifier=result_identifier,
                expectation_suite_identifier=expectation_suite_identifier,
            )
            action_results[result_identifier] = action_result

        return action_results

    @override
    def _run(  # type: ignore[override] # signature does not match parent  # noqa: PLR0913
        self,
        validation_result_suite: ExpectationSuiteValidationResult,
        validation_result_suite_identifier: Union[ValidationResultIdentifier, GXCloudIdentifier],
        payload=None,
        expectation_suite_identifier=None,
        checkpoint_identifier=None,
    ):
        logger.debug("UpdateDataDocsAction.run")

        if validation_result_suite is None:
            logger.warning(
                f"No validation_result_suite was passed to {type(self).__name__} action. Skipping action."  # noqa: E501
            )
            return

        if not isinstance(
            validation_result_suite_identifier,
            (ValidationResultIdentifier, GXCloudIdentifier),
        ):
            raise TypeError(
                "validation_result_id must be of type ValidationResultIdentifier or GeCloudIdentifier, not {}".format(  # noqa: E501
                    type(validation_result_suite_identifier)
                )
            )

        # TODO Update for RenderedDataDocs
        # build_data_docs will return the index page for the validation results, but we want to return the url for the validation result using the code below  # noqa: E501
        self._build_data_docs(
            site_names=self.site_names,
            resource_identifiers=[
                validation_result_suite_identifier,
                expectation_suite_identifier,
            ],
        )
        data_docs_validation_results: dict = {}
        if self._using_cloud_context:
            return data_docs_validation_results

        # get the URL for the validation result
        docs_site_urls_list = self._get_docs_sites_urls(
            resource_identifier=validation_result_suite_identifier,
            site_names=self.site_names,
        )
        # process payload
        for sites in docs_site_urls_list:
            data_docs_validation_results[sites["site_name"]] = sites["site_url"]

        return data_docs_validation_results


@public_api
class SNSNotificationAction(ValidationAction):
    """Action that pushes validations results to an SNS topic with a subject of passed or failed.

    YAML configuration example:

        ```yaml
        - name: send_sns_notification_on_validation_result
        action:
          class_name: SNSNotificationAction
          # put the actual SNS Arn in the uncommitted/config_variables.yml file
          # or pass in as environment variable
          sns_topic_arn:
          sns_subject:
        ```

    Args:
        sns_topic_arn: The SNS Arn to publish messages to.
        sns_subject: Optional. The SNS Message Subject - defaults to expectation_suite_identifier.name.
    """  # noqa: E501

    type: Literal["sns"] = "sns"

    sns_topic_arn: str
    sns_message_subject: Optional[str]

    @override
    def v1_run(self, checkpoint_result: CheckpointResult) -> str:
        return self._send_sns_notification(
            sns_topic_arn=self.sns_topic_arn,
            sns_subject=self.sns_message_subject or checkpoint_result.name,
            validation_results=json.dumps(
                [result.to_json_dict() for result in checkpoint_result.run_results.values()],
                indent=4,
            ),
        )

    @override
    def _run(  # type: ignore[override] # signature does not match parent
        self,
        validation_result_suite: ExpectationSuiteValidationResult,
        validation_result_suite_identifier: ValidationResultIdentifier,
        expectation_suite_identifier=None,
        checkpoint_identifier=None,
        **kwargs,
    ) -> str:
        logger.debug("SNSNotificationAction.run")

        if validation_result_suite is None:
            logger.warning(
                f"No validation_result_suite was passed to {type(self).__name__} action. Skipping action. "  # noqa: E501
            )

        if self.sns_message_subject is None:
            logger.warning("No message subject was passed checking for expectation_suite_name")
            if expectation_suite_identifier is None:
                subject = validation_result_suite_identifier.run_id
                logger.warning(
                    f"No expectation_suite_identifier was passed. Defaulting to validation run_id: {subject}."  # noqa: E501
                )
            else:
                subject = expectation_suite_identifier.name
                logger.info(f"Using expectation_suite_name: {subject}")
        else:
            subject = self.sns_message_subject

        return self._send_sns_notification(
            self.sns_topic_arn, subject, validation_result_suite.__str__(), **kwargs
        )

    @staticmethod
    def _send_sns_notification(
        sns_topic_arn: str, sns_subject: str, validation_results: str, **kwargs
    ) -> str:
        """
        Send JSON results to an SNS topic with a schema of:


        :param sns_topic_arn:  The SNS Arn to publish messages to
        :param sns_subject: : The SNS Message Subject - defaults to suite_identifier.name
        :param validation_results:  The results of the validation ran
        :param kwargs:  Keyword arguments to pass to the boto3 Session
        :return:  Message ID that was published or error message

        """
        if not aws.boto3:
            logger.warning("boto3 is not installed")
            return "boto3 is not installed"

        message_dict = {
            "TopicArn": sns_topic_arn,
            "Subject": sns_subject,
            "Message": json.dumps(validation_results),
            "MessageAttributes": {
                "String": {"DataType": "String.Array", "StringValue": "ValidationResults"},
            },
            "MessageStructure": "json",
        }
        session = aws.boto3.Session(**kwargs)
        sns = session.client("sns")
        try:
            response = sns.publish(**message_dict)
        except sns.exceptions.InvalidParameterException:
            error_msg = f"Received invalid for message: {validation_results}"
            logger.error(error_msg)  # noqa: TRY400
            return error_msg
        else:
            return (
                f"Successfully posted results to {response['MessageId']} with Subject {sns_subject}"
            )


class APINotificationAction(ValidationAction):
    type: Literal["api"] = "api"

    url: str

    @override
    def v1_run(self, checkpoint_result: CheckpointResult) -> str:
        aggregate_payload = []
        for run_id, run_result in checkpoint_result.run_results.items():
            suite_name = run_result.suite_name
            serializable_results = convert_to_json_serializable(run_result.results)
            batch_identifier = run_id.batch_identifier

            payload = self.create_payload(
                data_asset_name=batch_identifier,
                suite_name=suite_name,
                validation_results_serializable=serializable_results,
            )
            aggregate_payload.append(payload)

        response = self.send_results(aggregate_payload)
        return f"Posted results to API, status code - {response.status_code}"

    @override
    def _run(  # type: ignore[override] # signature does not match parent
        self,
        validation_result_suite: ExpectationSuiteValidationResult,
        validation_result_suite_identifier: ValidationResultIdentifier,
        expectation_suite_identifier: Optional[ExpectationSuiteIdentifier] = None,
        checkpoint_identifier=None,
        **kwargs,
    ):
        suite_name: str = validation_result_suite.suite_name
        data_asset_name: str = validation_result_suite.asset_name or "__no_data_asset_name__"

        validation_results: list = validation_result_suite.results
        validation_results_serializable: list = convert_to_json_serializable(validation_results)

        payload = self.create_payload(
            data_asset_name=data_asset_name,
            suite_name=suite_name,
            validation_results_serializable=validation_results_serializable,
        )

        response = self.send_results(payload)
        return f"Successfully Posted results to API, status code - {response.status_code}"

    def send_results(self, payload) -> requests.Response:
        try:
            headers = {"Content-Type": "application/json"}
            return requests.post(self.url, headers=headers, data=payload)
        except Exception as e:
            print(f"Exception when sending data to API - {e}")
            raise e  # noqa: TRY201

    @staticmethod
    def create_payload(data_asset_name, suite_name, validation_results_serializable) -> dict:
        return {
            "test_suite_name": suite_name,
            "data_asset_name": data_asset_name,
            "validation_results": validation_results_serializable,
        }
