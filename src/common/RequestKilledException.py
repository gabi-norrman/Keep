class RequestKilledException(Exception):
    """Raised inside a request's processing when that request has been killed or superseded."""
