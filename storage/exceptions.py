"""Storage 模块自定义异常。"""


class StorageError(Exception):
    """存储层基础异常。"""


class EncryptionError(StorageError):
    """加密/解密失败。"""


class DatabaseError(StorageError):
    """数据库操作失败。"""
