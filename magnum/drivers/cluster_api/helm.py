# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import functools
import json
import pathlib
import typing as t

from oslo_concurrency import processutils
from oslo_log import log as logging

from magnum.common import utils


def mergeconcat(defaults, *overrides):
    """Deep-merge two or more dictionaries together.

    Lists are concatenated.
    """

    def mergeconcat2(defaults, overrides):
        if isinstance(defaults, dict) and isinstance(overrides, dict):
            merged = dict(defaults)
            for key, value in overrides.items():
                if key in defaults:
                    merged[key] = mergeconcat2(defaults[key], value)
                else:
                    merged[key] = value
            return merged
        elif isinstance(defaults, (list, tuple)) and isinstance(
            overrides, (list, tuple)
        ):
            merged = list(defaults)
            merged.extend(overrides)
            return merged
        else:
            return overrides if overrides is not None else defaults

    return functools.reduce(mergeconcat2, overrides, defaults)


class Client:
    """Client for interacting with Helm."""

    def __init__(
        self,
        *,
        default_timeout: t.Union[int, str] = "5m",
        executable: str = "helm",
        history_max_revisions: int = 10,
        insecure_skip_tls_verify: bool = False,
        kubeconfig: t.Optional[pathlib.Path] = None,
        unpack_directory: t.Optional[str] = None
    ):
        self._logger = logging.getLogger(__name__)
        self._default_timeout = default_timeout
        self._executable = executable
        self._history_max_revisions = history_max_revisions
        self._insecure_skip_tls_verify = insecure_skip_tls_verify
        self._kubeconfig = kubeconfig
        self._unpack_directory = unpack_directory

    def _log_format(self, argument):
        argument = str(argument)
        if argument == "-":
            return "<stdin>"
        elif "\n" in argument:
            return "<multi-line string>"
        else:
            return argument

    def _run(self, command, **kwargs) -> bytes:
        command = [self._executable] + command
        if self._kubeconfig:
            command.extend(["--kubeconfig", self._kubeconfig])
        stdout, stderr = utils.execute(*command, **kwargs)
        return stdout

    def install_or_upgrade(
        self,
        release_name: str,
        chart_ref: t.Union[pathlib.Path, str],
        *values: t.Dict[str, t.Any],
        create_namespace: bool = False,
        dry_run: bool = False,
        force: bool = False,
        namespace: t.Optional[str] = None,
        repo: t.Optional[str] = None,
        timeout: t.Union[int, str, None] = None,
        version: t.Optional[str] = None,
        wait: bool = False
    ) -> t.Iterable[t.Dict[str, t.Any]]:
        """Install or upgrade specified release using chart and values."""
        command = [
            "upgrade",
            release_name,
            chart_ref,
            "--history-max",
            self._history_max_revisions,
            "--install",
            "--output",
            "json",
            # Use the default timeout unless an override is specified
            "--timeout",
            timeout if timeout is not None else self._default_timeout,
            # We send the values in on stdin
            "--values",
            "-",
        ]
        if create_namespace:
            command.append("--create-namespace")
        if dry_run:
            command.append("--dry-run")
        if force:
            command.append("--force")
        if self._insecure_skip_tls_verify:
            command.append("--insecure-skip-tls-verify")
        if namespace:
            command.extend(["--namespace", namespace])
        if repo:
            command.extend(["--repo", repo])
        if version:
            command.extend(["--version", version])
        if wait:
            command.extend(["--wait", "--wait-for-jobs"])
        process_input = json.dumps(mergeconcat({}, *values))
        return json.loads(self._run(command, process_input=process_input))

    def uninstall_release(
        self,
        release_name: str,
        *,
        dry_run: bool = False,
        keep_history: bool = False,
        namespace: t.Optional[str] = None,
        no_hooks: bool = False,
        timeout: t.Union[int, str, None] = None,
        wait: bool = False
    ):
        """Uninstall the named release."""
        command = [
            "uninstall",
            release_name,
            # Use the default timeout unless an override is specified
            "--timeout",
            timeout if timeout is not None else self._default_timeout,
        ]
        if dry_run:
            command.append("--dry-run")
        if keep_history:
            command.append("--keep-history")
        if namespace:
            command.extend(["--namespace", namespace])
        if no_hooks:
            command.append("--no-hooks")
        if wait:
            command.append("--wait")
        try:
            self._run(command)
        except processutils.ProcessExecutionError as exc:
            # Swallow release not found errors, as that is our desired state
            if "release: not found" not in exc.stderr:
                raise
