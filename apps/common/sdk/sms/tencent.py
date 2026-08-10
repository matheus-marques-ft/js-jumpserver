from collections import OrderedDict

from django.conf import settings
from common.exceptions import JMSException
from common.utils import get_logger
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
# Import the client models of the corresponding product module.
from tencentcloud.sms.v20210111 import sms_client, models
# Import optional configuration classes
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile

from .base import BaseSMSClient

logger = get_logger(__file__)


class TencentSMS(BaseSMSClient):
    """
    https://cloud.tencent.com/document/product/382/43196#.E5.8F.91.E9.80.81.E7.9F.AD.E4.BF.A1
    """
    SIGN_AND_TMPL_SETTING_FIELD_PREFIX = 'TENCENT'

    @classmethod
    def new_from_settings(cls):
        return cls(
            secret_id=settings.TENCENT_SECRET_ID,
            secret_key=settings.TENCENT_SECRET_KEY,
            sdkappid=settings.TENCENT_SDKAPPID
        )

    def __init__(self, secret_id: str, secret_key: str, sdkappid: str):
        self.sdkappid = sdkappid

        cred = credential.Credential(secret_id, secret_key)
        httpProfile = HttpProfile()
        httpProfile.reqMethod = "POST"  # POST request (POST is the default)
        httpProfile.reqTimeout = 30    # Request timeout, in seconds (default 60 seconds)
        httpProfile.endpoint = "sms.tencentcloudapi.com"

        clientProfile = ClientProfile()
        clientProfile.signMethod = "TC3-HMAC-SHA256"  # Specify the signing algorithm
        clientProfile.language = "en-US"
        clientProfile.httpProfile = httpProfile
        self.client = sms_client.SmsClient(cred, "ap-guangzhou", clientProfile)

    def send_sms(self, phone_numbers: list, sign_name: str, template_code: str, template_param: OrderedDict, **kwargs):
        try:
            req = models.SendSmsRequest()
            # Setting basic types:
            # The SDK uses a pointer-style approach for parameters, so even for
            # basic types you need to assign values via a pointer.
            # The SDK provides wrapper functions for pointer references to basic types
            # Helpful links:
            # SMS console: https://console.cloud.tencent.com/smsv2
            # sms helper: https://cloud.tencent.com/document/product/382/3773

            # SMS application ID: the actual SdkAppId generated after adding an
            # application in the [SMS console], e.g. 1400006666
            req.SmsSdkAppId = self.sdkappid
            # SMS signature content: UTF-8 encoded, must be an approved signature;
            # signature info can be viewed by logging into the [SMS console]
            req.SignName = sign_name
            # SMS extension code: not enabled by default, contact [sms helper] to enable
            req.ExtendCode = ""
            # User session content: can carry client-side context such as user
            # IDs; the server returns it unchanged
            req.SessionContext = "Jumpserver"
            # International/Hong Kong-Macao-Taiwan SMS senderid: leave blank for
            # domestic SMS, not enabled by default, contact [sms helper] to enable
            req.SenderId = ""
            # Recipient phone numbers, using the E.164 standard, +[country or
            # region code][phone number]
            # Example: +8613711112222, with a leading +, 86 as country code,
            # 13711112222 as the phone number; no more than 200 numbers
            req.PhoneNumberSet = phone_numbers
            # Template ID: must be an approved template ID. The template ID can
            # be viewed by logging into the [SMS console]
            req.TemplateId = template_code
            # Template parameters: set to empty if there are none
            req.TemplateParamSet = list(template_param.values())
            # Call the DescribeInstances method via the client object to send
            # the request. Note the request method name corresponds to the request object.
            # The returned resp is an instance of the DescribeInstancesResponse
            # class, corresponding to the request object.
            logger.info(f'Tencent sms send: '
                        f'phone_numbers={phone_numbers} '
                        f'sign_name={sign_name} '
                        f'template_code={template_code} '
                        f'template_param={template_param}')

            resp = self.client.SendSms(req)

            try:
                code = resp.SendStatusSet[0].Code
                msg = resp.SendStatusSet[0].Message
            except IndexError:
                raise JMSException(code='response_bad', detail=resp)

            if code.lower() != 'ok':
                raise JMSException(code=code, detail=msg)

            return resp
        except TencentCloudSDKException as e:
            raise JMSException(code=e.code, detail=e.message)


client = TencentSMS
