import posixpath
import urllib.parse
from collections.abc import Generator
from typing import Any

import oss2 as aliyun_s3  # type: ignore


from configs import dify_config
from extensions.storage.base_storage import BaseStorage
from libs.helper import get_readable_file_size
from oss2.models import GetObjectResult


class AliyunOssStorage(BaseStorage):
    """Implementation for Aliyun OSS storage."""

    def __init__(self):
        super().__init__()
        self.bucket_name = dify_config.ALIYUN_OSS_BUCKET_NAME
        self.folder = dify_config.ALIYUN_OSS_PATH
        oss_auth_method = aliyun_s3.Auth
        region = None
        is_cname = dify_config.ALIYUN_OSS_IS_CNAME
        if dify_config.ALIYUN_OSS_AUTH_VERSION == "v4":
            oss_auth_method = aliyun_s3.AuthV4
            region = dify_config.ALIYUN_OSS_REGION
        oss_auth = oss_auth_method(dify_config.ALIYUN_OSS_ACCESS_KEY, dify_config.ALIYUN_OSS_SECRET_KEY)
        self.client = aliyun_s3.Bucket(
            oss_auth,
            dify_config.ALIYUN_OSS_ENDPOINT,
            self.bucket_name,
            connect_timeout=30,
            region=region,
            is_cname=is_cname
        )

    def save(self, filename, data):
        self.client.put_object(self.__wrapper_folder_filename(filename), data)

    def load_once(self, filename: str) -> bytes:
        try:
            obj = self.client.get_object(self.__wrapper_folder_filename(filename))
            data: bytes = obj.read()
            return data
        except:
            return None

    def load_stream(self, filename: str) -> Generator:
        obj = self.client.get_object(self.__wrapper_folder_filename(filename))
        while chunk := obj.read(4096):
            yield chunk

    def download(self, filename: str, target_filepath):
        self.client.get_object_to_file(self.__wrapper_folder_filename(filename), target_filepath)

    def exists(self, filename: str):
        return self.client.object_exists(self.__wrapper_folder_filename(filename))

    def delete(self, filename: str):
        self.client.delete_object(self.__wrapper_folder_filename(filename))

    def __wrapper_folder_filename(self, filename: str) -> str:
        return posixpath.join(self.folder, filename) if self.folder else filename

    def sign_url(self, filename: str, params=None) -> str:
        try:
            return self.client.sign_url('GET', filename, 8 * 60 * 60, params=params, slash_safe=True)
        except:
            return ""

    def get(self, filename: str) -> GetObjectResult | None:
        try:
            return self.client.get_object(filename)
        except:
            pass

    def referencefile(self, filename: str) -> None | dict[Any, Any] | dict[str, str | Any]:
        resource = {}
        try:
            realkey = f"dify/upload_files/source_files/{filename}"
            paras = dict()
            paras["response-content-disposition"] = "attachment; filename=" + urllib.parse.quote(filename,
                                                                                                 encoding="utf-8")
            resource["downloadurl"] = self.sign_url(realkey, paras)
            resource["name"] = filename
            resource["url"] = self.sign_url(realkey)
            resource["size"] = 0
            resource["sizeunit"] = "0kb"
            ossfile = self.get(realkey) or None
            if ossfile:
                size = ossfile.content_length
                sizeunit = get_readable_file_size(size)
                resource["size"] = size
                resource["sizeunit"] = sizeunit
            return resource
        except:
            return None
