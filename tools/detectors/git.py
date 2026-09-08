"""Repository-level Git observations."""

from landscape_core.git_repository import remote_url
from landscape_core.observations import observation


class GitDetector:
    name = "git"
    version = 1

    def detect(self, root, repository, commit):
        values = [
            ("git-commit", {"sha": commit}),
            ("git-remote", {"origin": remote_url(root)}),
        ]
        return [
            observation(
                kind=kind,
                repository=repository,
                commit=commit,
                detector=self.name,
                detector_version=self.version,
                value=value,
            )
            for kind, value in values
        ], []

