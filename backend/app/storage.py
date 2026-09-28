"""Object storage for insurance card photos and generated PDFs.

S3 objects are written with SSE-KMS under the application's customer-managed
key; the bucket policy (see infra) additionally denies non-TLS access and any
unencrypted PUT. Files are always streamed back through the API (never via
public or presigned URLs) so every view is authorized and audit-logged.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Protocol

from .config import get_settings


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class S3Storage:
    def __init__(self, bucket: str, kms_key_id: str, region: str):
        import boto3

        self._bucket = bucket
        self._kms_key_id = kms_key_id
        self._s3 = boto3.client("s3", region_name=region)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=self._kms_key_id,
            BucketKeyEnabled=True,
        )

    def get(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=key)


class LocalStorage:
    """Dev/test only. Files are still encrypted by the caller before writing."""

    def __init__(self, root: str):
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self._root / key).resolve()
        if self._root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put(self, key: str, data: bytes, content_type: str) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "s3":
        return S3Storage(s.s3_bucket, s.kms_key_id, s.aws_region)
    return LocalStorage(s.local_storage_dir)
