import logging

from flask import request
from flask_restful import Resource, reqparse
from werkzeug.exceptions import InternalServerError, NotFound

import services
from controllers.service_api import api
from controllers.service_api.app.error import (
    AppUnavailableError,
    CompletionRequestError,
    ConversationCompletedError,
    NotChatAppError,
    ProviderModelCurrentlyNotSupportError,
    ProviderNotInitializeError,
    ProviderQuotaExceededError,
)
from controllers.service_api.wraps import FetchUserArg, WhereisUserArg, validate_app_token
from controllers.web.error import InvokeRateLimitError as InvokeRateLimitHttpError
from core.app.apps.base_app_queue_manager import AppQueueManager
from core.app.entities.app_invoke_entities import InvokeFrom
from core.errors.error import (
    ModelCurrentlyNotSupportError,
    ProviderTokenNotInitError,
    QuotaExceededError,
)
from core.helper.trace_id_helper import get_external_trace_id
from core.model_runtime.errors.invoke import InvokeError
from libs import helper
from libs.helper import uuid_value
from models.model import App, AppMode, EndUser
from services.app_generate_service import AppGenerateService
from services.errors.llm import InvokeRateLimitError


class CompletionApi(Resource):
    @validate_app_token(fetch_user_arg=FetchUserArg(fetch_from=WhereisUserArg.JSON, required=True))
    def post(self, app_model: App, end_user: EndUser):
        if app_model.mode != "completion":
            raise AppUnavailableError()

        parser = reqparse.RequestParser()
        parser.add_argument("inputs", type=dict, required=True, location="json")
        parser.add_argument("query", type=str, location="json", default="")
        parser.add_argument("files", type=list, required=False, location="json")
        parser.add_argument("response_mode", type=str, choices=["blocking", "streaming"], location="json")
        parser.add_argument("retriever_from", type=str, required=False, default="dev", location="json")

        args = parser.parse_args()

        streaming = args["response_mode"] == "streaming"

        args["auto_generate_name"] = False

        try:
            response = AppGenerateService.generate(
                app_model=app_model,
                user=end_user,
                args=args,
                invoke_from=InvokeFrom.SERVICE_API,
                streaming=streaming,
            )

            return helper.compact_generate_response(response)
        except services.errors.conversation.ConversationNotExistsError:
            raise NotFound("Conversation Not Exists.")
        except services.errors.conversation.ConversationCompletedError:
            raise ConversationCompletedError()
        except services.errors.app_model_config.AppModelConfigBrokenError:
            logging.exception("App model config broken.")
            raise AppUnavailableError()
        except ProviderTokenNotInitError as ex:
            raise ProviderNotInitializeError(ex.description)
        except QuotaExceededError:
            raise ProviderQuotaExceededError()
        except ModelCurrentlyNotSupportError:
            raise ProviderModelCurrentlyNotSupportError()
        except InvokeError as e:
            raise CompletionRequestError(e.description)
        except ValueError as e:
            raise e
        except Exception:
            logging.exception("internal server error.")
            raise InternalServerError()


class CompletionStopApi(Resource):
    @validate_app_token(fetch_user_arg=FetchUserArg(fetch_from=WhereisUserArg.JSON, required=True))
    def post(self, app_model: App, end_user: EndUser, task_id):
        if app_model.mode != "completion":
            raise AppUnavailableError()

        AppQueueManager.set_stop_flag(task_id, InvokeFrom.SERVICE_API, end_user.id)

        return {"result": "success"}, 200


class ChatApi(Resource):
    @validate_app_token(fetch_user_arg=FetchUserArg(fetch_from=WhereisUserArg.JSON, required=True))
    def post(self, app_model: App, end_user: EndUser):
        app_mode = AppMode.value_of(app_model.mode)
        if app_mode not in {AppMode.CHAT, AppMode.AGENT_CHAT, AppMode.ADVANCED_CHAT}:
            raise NotChatAppError()

        parser = reqparse.RequestParser()
        parser.add_argument("inputs", type=dict, required=True, location="json")
        parser.add_argument("query", type=str, required=True, location="json")
        parser.add_argument("files", type=list, required=False, location="json")
        parser.add_argument("response_mode", type=str, choices=["blocking", "streaming"], location="json")
        parser.add_argument("conversation_id", type=uuid_value, location="json")
        parser.add_argument("retriever_from", type=str, required=False, default="dev", location="json")
        parser.add_argument("auto_generate_name", type=bool, required=False, default=True, location="json")

        args = parser.parse_args()
        REPLACEMENT_MAP = {
            "邦成": "BC",
            "亚联财": "YLC",
            "DJ小贷": "DJXD",
            "渤海": "BH",
            "国民": "GM",
            "光大": "GD",
            "陕国投": "SGT",
            "外贸": "WM",
            "东莞": "DG",
            "民生": "MS",
            "爱建": "AJ",
            "洛阳": "LY",
            "中海": "ZH",
            "中诚": "ZC",
            "蓝海": "LH",
            "中关村": "ZGC",
            "齐鲁": "QL",
            "新网": "XW",
            "汉口": "HK",
            "浙商": "ZS",
            "亿联": "YL",
            "三湘": "SX"
        }
        REPLACEMENT_MAP2 = {
            "BH": "BH信托",
            "GM": "GM信托",
            "GD": "GD信托",
            "SGT": "SGT信托",
            "WM": "WM信托",
            "DG": "DG信托",
            "MS": "MS银行",
            "AJ": "AJ信托",
            "LY": "LY银行",
            "ZH": "ZH信托",
            "ZC": "ZC信托",
            "LH": "LH银行",
            "ZGC": "ZGC银行",
            "QL": "QL银行",
            "XW": "XW银行",
            "HK": "HK银行",
            "ZS": "ZS银行",
            "YL": "YL银行",
            "SX": "SX银行"
        }
        # 替换query中的内容
        query = args["query"]
        new_query = self.replace_with_map(query, REPLACEMENT_MAP2)
        new_query = ''.join(char for char in new_query if char.isprintable() and not char.isspace())
        # query_isvalidate = bool(re.search(r'\S', new_query))

        for old_str, new_str in REPLACEMENT_MAP.items():
            new_query = new_query.replace(old_str, new_str)
        args["query"] = new_query


        external_trace_id = get_external_trace_id(request)
        if external_trace_id:
            args["external_trace_id"] = external_trace_id

        streaming = args["response_mode"] == "streaming"

        try:
            response = AppGenerateService.generate(
                app_model=app_model, user=end_user, args=args, invoke_from=InvokeFrom.SERVICE_API, streaming=streaming
            )

            return helper.compact_generate_response(response)
        except services.errors.conversation.ConversationNotExistsError:
            raise NotFound("Conversation Not Exists.")
        except services.errors.conversation.ConversationCompletedError:
            raise ConversationCompletedError()
        except services.errors.app_model_config.AppModelConfigBrokenError:
            logging.exception("App model config broken.")
            raise AppUnavailableError()
        except ProviderTokenNotInitError as ex:
            raise ProviderNotInitializeError(ex.description)
        except QuotaExceededError:
            raise ProviderQuotaExceededError()
        except ModelCurrentlyNotSupportError:
            raise ProviderModelCurrentlyNotSupportError()
        except InvokeRateLimitError as ex:
            raise InvokeRateLimitHttpError(ex.description)
        except InvokeError as e:
            raise CompletionRequestError(e.description)
        except ValueError as e:
            raise e
        except Exception:
            logging.exception("操作异常,请稍后再试!")
            raise InternalServerError()

    def replace_with_map(self, text, replacement_map):
        for key, value in replacement_map.items():
            # 只有当key存在且value不在字符串中时才替换
            if key in text and value not in text:
                text = text.replace(key, value)
        return text

class ChatStopApi(Resource):
    @validate_app_token(fetch_user_arg=FetchUserArg(fetch_from=WhereisUserArg.JSON, required=True))
    def post(self, app_model: App, end_user: EndUser, task_id):
        app_mode = AppMode.value_of(app_model.mode)
        if app_mode not in {AppMode.CHAT, AppMode.AGENT_CHAT, AppMode.ADVANCED_CHAT}:
            raise NotChatAppError()

        AppQueueManager.set_stop_flag(task_id, InvokeFrom.SERVICE_API, end_user.id)

        return {"result": "success"}, 200


api.add_resource(CompletionApi, "/completion-messages")
api.add_resource(CompletionStopApi, "/completion-messages/<string:task_id>/stop")
api.add_resource(ChatApi, "/chat-messages")
api.add_resource(ChatStopApi, "/chat-messages/<string:task_id>/stop")
