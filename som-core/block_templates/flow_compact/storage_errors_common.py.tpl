class ObjectError(HTTPException):
    """Expose a fixed public status without SDK data."""
    status: ClassVar[int] = 503
    message: ClassVar[str] = "object storage unavailable"

    def __init__(self, *_details: object) -> None:
        super().__init__(self.status, self.message)


class ObjectMissing(ObjectError):
    """The requested object or version is absent."""
    status = 404
    message = "object not found"


class ObjectExists(ObjectError):
    """An independent write has different content."""
    status = 409
    message = "object contains different data"


class ObjectUnavailable(ObjectError):
    """The provider cannot prove a safe outcome."""
