class BusinessError(Exception):
    """可预期的业务规则错误，API 层转换为 400，Web 层转换为提示。"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
