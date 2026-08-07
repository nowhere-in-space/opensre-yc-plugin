"""Yandex Cloud environment variable names.

The names deliberately match the ones the official ``yc`` CLI and Yandex SDKs
already read, so a shell that can run ``yc`` can run OpenSRE without a second
set of exports.
"""

from __future__ import annotations

YC_CLOUD_ID_ENV = "YC_CLOUD_ID"
YC_FOLDER_ID_ENV = "YC_FOLDER_ID"

#: Folder that qualifies a bare AI Studio model name into a gpt:// URI.
#: Separate from YC_FOLDER_ID on purpose: the models and the infrastructure
#: an investigation reads can live in different folders.
YANDEX_FOLDER_ID_ENV = "YANDEX_FOLDER_ID"
YC_SA_KEY_FILE_ENV = "YC_SA_KEY_FILE"
YC_SA_KEY_ENV = "YC_SA_KEY"
YC_TOKEN_ENV = "YC_TOKEN"
YC_IAM_TOKEN_ENV = "YC_IAM_TOKEN"
YC_USE_METADATA_ENV = "YC_USE_METADATA"
YC_API_ENDPOINT_ENV = "YC_API_ENDPOINT"
YC_ENDPOINT_OVERRIDES_ENV = "YC_ENDPOINT_OVERRIDES"
YC_INSTANCES_ENV = "YC_INSTANCES"

__all__ = [
    "YANDEX_FOLDER_ID_ENV",
    "YC_API_ENDPOINT_ENV",
    "YC_CLOUD_ID_ENV",
    "YC_ENDPOINT_OVERRIDES_ENV",
    "YC_FOLDER_ID_ENV",
    "YC_IAM_TOKEN_ENV",
    "YC_INSTANCES_ENV",
    "YC_SA_KEY_ENV",
    "YC_SA_KEY_FILE_ENV",
    "YC_TOKEN_ENV",
    "YC_USE_METADATA_ENV",
]
