""" domain errors """

class IngestError(Exception):
    status_code= 500
    code="ingest_error"
    def __init__(self, message: str):
        super().__init__(message)
        self.message=message

class InvalidRepoURL(IngestError):
    status_code= 404
    code= "repo_not_found"

class RepoNotFound(IngestError):
    """Also used for private repos: we only support public ones."""
    status_code = 404
    code = "repo_not_found"

class RefNotFound(Exception):
    status_code = 404
    code = "ref_not_found"
class RepoTooLarge(Exception):
    status_code = 413
    code = "repo_too_large"

class GitHubUnavailable(IngestError):
    status_code = 502
    code = "github_error"


class GitHubRateLimited(GitHubUnavailable):
    status_code = 503
    code = "github_rate_limited"

class ToolError(Exception):
    """ tool call the model got wrong"""
    def __init__(self, message:str):
        super().__init__(message)   
        self.message= message