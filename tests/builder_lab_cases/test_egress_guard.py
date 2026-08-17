from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "apply_builder_egress_guard.sh"

PRIVATE_DESTINATIONS = [
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.88.99.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
]

LEGACY_DESTINATIONS = [
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "224.0.0.0/4",
    "240.0.0.0/4",
]

FAKE_XTABLES = r'''#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_IPTABLES_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
tool = Path(sys.argv[0]).name

def save(candidate):
    state_path.write_text(json.dumps(candidate, sort_keys=True), encoding="utf-8")

def dump(candidate):
    print("*filter")
    for chain, policy in candidate["chains"].items():
        print(f":{chain} {policy} [0:0]")
    for rule in candidate["rules"]:
        print(rule)
    print("COMMIT")

def normalize_rule(rule):
    match = re.fullmatch(
        r"(-A \S+) -i (\S+) -s (\S+) -d (\S+)(.*)",
        rule,
    )
    if match:
        prefix, interface, source, destination, remainder = match.groups()
        return (
            f"{prefix} -s {source} -d {destination} -i {interface}{remainder}"
        )
    return rule

if tool == "iptables-save":
    dump(state)
    raise SystemExit(0)

if tool == "iptables":
    args = sys.argv[1:]
    if args[:2] == ["-w", "10"]:
        args = args[2:]
    if args[:1] == ["-nL"]:
        raise SystemExit(0 if args[1] in state["chains"] else 1)
    if args[:1] == ["-S"]:
        chain = args[1]
        if chain not in state["chains"]:
            raise SystemExit(1)
        print(f"-N {chain}")
        for rule in state["rules"]:
            if rule.startswith(f"-A {chain} "):
                print(rule)
        raise SystemExit(0)
    raise SystemExit(f"unsupported fake iptables arguments: {args}")

if tool != "iptables-restore":
    raise SystemExit(f"unsupported fake tool: {tool}")

payload = sys.stdin.read()
if os.environ.get("FAKE_RESTORE_FAIL") == "1":
    raise SystemExit(41)

if os.environ.get("FAKE_CREATE_G5_BEFORE_RESTORE") == "1":
    state["chains"]["FOREIGN"] = "-"
    state["chains"]["KAIGO-BLD-FWD-G5"] = "-"
    state["chains"]["KAIGO-BLD-HOST-G5"] = "-"
    state["rules"].extend(
        [
            "-A FOREIGN -j KAIGO-BLD-FWD-G5",
            "-A KAIGO-BLD-FWD-G5 -j RETURN",
            "-A KAIGO-BLD-HOST-G5 -j RETURN",
        ]
    )
    save(state)

candidate = json.loads(json.dumps(state))
try:
    for line in payload.splitlines():
        if not line or line in {"*filter", "COMMIT"}:
            continue
        if line.startswith(":"):
            chain = line[1:].split(" ", 1)[0]
            if chain in candidate["chains"]:
                candidate["rules"] = [
                    rule for rule in candidate["rules"]
                    if not rule.startswith(f"-A {chain} ")
                ]
            else:
                candidate["chains"][chain] = "-"
        elif line.startswith("-N "):
            chain = line.split(" ", 1)[1]
            if chain in candidate["chains"]:
                raise ValueError(f"chain already exists: {chain}")
            candidate["chains"][chain] = "-"
        elif line.startswith("-A "):
            chain = line.split(" ", 2)[1]
            if chain not in candidate["chains"]:
                raise ValueError(f"missing append chain: {chain}")
            candidate["rules"].append(normalize_rule(line))
        elif line.startswith("-D "):
            canonical = "-A " + line[3:]
            candidate["rules"].remove(canonical)
        elif line.startswith("-I "):
            match = re.fullmatch(r"-I (\S+) 1 (.*)", line)
            if not match:
                raise ValueError(f"unsupported insert: {line}")
            chain, remainder = match.groups()
            canonical = normalize_rule(f"-A {chain} {remainder}")
            index = next(
                (i for i, rule in enumerate(candidate["rules"])
                 if rule.startswith(f"-A {chain} ")),
                len(candidate["rules"]),
            )
            candidate["rules"].insert(index, canonical)
        elif line.startswith("-F "):
            chain = line.split(" ", 1)[1]
            candidate["rules"] = [
                rule for rule in candidate["rules"]
                if not rule.startswith(f"-A {chain} ")
            ]
        elif line.startswith("-X "):
            chain = line.split(" ", 1)[1]
            if any(rule.endswith(f"-j {chain}") or rule.endswith(f"-g {chain}")
                   for rule in candidate["rules"]):
                raise ValueError(f"referenced chain: {chain}")
            del candidate["chains"][chain]
        else:
            raise ValueError(f"unsupported restore line: {line}")
except (KeyError, ValueError) as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(42)

save(candidate)
'''


def _body() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(name)}\(\) \{{\n(?P<body>.*?)^\}}$",
        source,
    )
    if match is None:
        raise AssertionError(f"missing shell function: {name}")
    return match.group("body")


class BuilderEgressGuardContractTests(unittest.TestCase):
    def test_g5_forward_chain_is_byte_for_byte_the_previous_g4_policy(self):
        source = _body()
        destinations_match = re.search(
            r"(?ms)^PRIVATE_DESTINATIONS=\(\n(?P<items>.*?)^\)$", source
        )
        self.assertIsNotNone(destinations_match)
        destinations = [
            line.strip()
            for line in destinations_match.group("items").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            destinations,
            [
                "0.0.0.0/8",
                "10.0.0.0/8",
                "100.64.0.0/10",
                "127.0.0.0/8",
                "169.254.0.0/16",
                "172.16.0.0/12",
                "192.0.0.0/24",
                "192.0.2.0/24",
                "192.88.99.0/24",
                "192.168.0.0/16",
                "198.18.0.0/15",
                "198.51.100.0/24",
                "203.0.113.0/24",
                "224.0.0.0/4",
                "240.0.0.0/4",
            ],
        )
        forward_body = _function_body(source, "expected_forward_chain")
        self.assertEqual(
            forward_body,
            '  echo "-N ${FORWARD_CHAIN}"\n'
            '  for network in "${PRIVATE_DESTINATIONS[@]}"; do\n'
            '    echo "-A ${FORWARD_CHAIN} -s ${SUBNET} -d ${network} '
            '-m conntrack --ctstate NEW -j REJECT '
            '--reject-with icmp-port-unreachable"\n'
            "  done\n"
            '  echo "-A ${FORWARD_CHAIN} -j RETURN"\n',
        )

    def test_g5_host_chain_has_only_exact_service_allows_before_reject(self):
        source = _body()
        self.assertIn('RULESET_GENERATION="G5"', source)
        host_body = _function_body(source, "expected_host_chain")
        rules = re.findall(r'^  echo "(?P<rule>-A .*?)"$', host_body, re.MULTILINE)
        self.assertEqual(
            rules,
            [
                "-A ${HOST_CHAIN} -s ${SUBNET} -d ${TUNNEL_HOST} -i ${BRIDGE} "
                "-p tcp -m tcp --dport ${TUNNEL_PORT} -m conntrack "
                "--ctstate NEW,ESTABLISHED -j ACCEPT",
                "-A ${HOST_CHAIN} -s ${BUILD_CLIENT}/32 -d ${PUBLIC_HTTPS_HOST} "
                "-i ${BRIDGE} -p tcp -m tcp --dport ${PUBLIC_HTTPS_PORT} "
                "-m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT",
                "-A ${HOST_CHAIN} -s ${SUBNET} -m conntrack --ctstate NEW "
                "-j REJECT --reject-with icmp-port-unreachable",
                "-A ${HOST_CHAIN} -j RETURN",
            ],
        )
        self.assertIn('TUNNEL_HOST="172.19.0.1/32"', source)
        self.assertIn('TUNNEL_PORT="8787"', source)
        self.assertIn('BRIDGE="br-kaigo-build"', source)
        self.assertIn('SUBNET="172.30.240.0/28"', source)

    def test_public_https_destination_is_required_and_strictly_validated(self):
        source = _body()
        validation = _function_body(source, "validate_public_https_destination")
        ipv4_validation = _function_body(source, "validate_ipv4")
        validation_call = 'validate_public_https_destination'

        self.assertIn(': "${KAIGO_WORKER_PUBLIC_HTTPS_HOST:?', source)
        self.assertIn(': "${KAIGO_WORKER_PUBLIC_HTTPS_PORT:?', source)
        self.assertIn(': "${KAIGO_WORKER_BUILD_CLIENT_ADDRESS:?', source)
        self.assertNotIn("5.129.236.90", source)
        self.assertIn('/32$', validation)
        self.assertIn("IFS='.'", ipv4_validation)
        self.assertIn("octet", ipv4_validation)
        self.assertIn('validate_ipv4 "${KAIGO_WORKER_BUILD_CLIENT_ADDRESS}"', validation)
        self.assertIn('[[ "${KAIGO_WORKER_PUBLIC_HTTPS_PORT}" =~ ^[0-9]+$ ]]', validation)
        self.assertIn("10#", validation)
        self.assertIn('<= 65535', validation)
        self.assertLess(source.index(validation_call), source.index('saved_rules="$(iptables-save'))

    def test_unreachable_generation_is_built_with_the_same_exact_host_contract(self):
        source = _body()
        build_start = source.index('transaction="$(mktemp)"')
        build_end = source.index('"${RESTORE[@]}" < "${transaction}"', build_start)
        build = source[build_start:build_end]
        exact_allow = (
            'echo "-A ${HOST_CHAIN} -i ${BRIDGE} -s ${SUBNET} '
            '-d ${TUNNEL_HOST} -p tcp -m tcp --dport ${TUNNEL_PORT} '
            '-m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT"'
        )
        public_allow = (
            'echo "-A ${HOST_CHAIN} -i ${BRIDGE} -s ${BUILD_CLIENT}/32 '
            '-d ${PUBLIC_HTTPS_HOST} -p tcp -m tcp --dport ${PUBLIC_HTTPS_PORT} '
            '-m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT"'
        )
        exact_reject = (
            'echo "-A ${HOST_CHAIN} -s ${SUBNET} -m conntrack '
            '--ctstate NEW -j REJECT --reject-with icmp-port-unreachable"'
        )
        self.assertEqual(build.count(exact_allow), 1)
        self.assertEqual(build.count(public_allow), 1)
        self.assertEqual(build.count(exact_reject), 1)
        self.assertLess(build.index(exact_allow), build.index(exact_reject))
        self.assertLess(build.index(public_allow), build.index(exact_reject))
        self.assertNotIn("0.0.0.0/0", build)
        self.assertNotIn("--dport 8787 -j ACCEPT", build)
        self.assertNotIn(
            '-s ${SUBNET} -d ${PUBLIC_HTTPS_HOST}',
            build,
        )
        self.assertNotIn('-i ${DATABASE_BRIDGE}', build)

    def test_partial_or_mutated_generation_fails_before_hook_cutover(self):
        source = _body()
        mismatch = source.index('if [[ "${forward_exists}" != "${host_exists}" ]]')
        build = source.index('transaction="$(mktemp)"')
        verify_forward = source.index(
            'verify_exact_chain "${FORWARD_CHAIN}" expected_forward_chain', mismatch
        )
        verify_host = source.index(
            'verify_exact_chain "${HOST_CHAIN}" expected_host_chain', mismatch
        )
        self.assertLess(mismatch, build)
        self.assertLess(verify_forward, build)
        self.assertLess(verify_host, build)
        self.assertIn("incomplete immutable egress generation", source[mismatch:build])
        self.assertIn("refusing in-place hook repair", source[mismatch:build])

    def test_generation_creation_and_hook_cutover_share_one_restore_transaction(self):
        source = _body()
        self.assertEqual(source.count('"${RESTORE[@]}" < "${transaction}"'), 1)
        self.assertNotIn('} | "${RESTORE[@]}"', source)
        transaction_start = source.index('transaction="$(mktemp)"')
        transaction_end = source.index('"${RESTORE[@]}" < "${transaction}"')
        transaction = source[transaction_start:transaction_end]
        self.assertLess(
            transaction.index('echo "-N ${FORWARD_CHAIN}"'),
            transaction.index('echo "-I DOCKER-USER 1'),
        )
        self.assertLess(
            transaction.index('echo "-N ${HOST_CHAIN}"'),
            transaction.index('echo "-I INPUT 1'),
        )
        self.assertIn("refusing in-place hook repair", source)

    def test_post_state_verification_is_fail_closed_and_checks_first_position(self):
        source = _body()
        restore = source.index('"${RESTORE[@]}" < "${transaction}"')
        post = source[restore:]
        verification = _function_body(source, "verify_post_state")
        self.assertIn("verify_post_state", post)
        self.assertIn('verify_exact_chain "${FORWARD_CHAIN}" expected_forward_chain', post)
        self.assertIn('verify_exact_chain "${HOST_CHAIN}" expected_host_chain', post)
        self.assertIn("unexpected Kaigo bridge hook", verification)
        self.assertIn("current Kaigo bridge hook is not first", verification)
        self.assertIn("stale Kaigo egress generation remains", verification)
        self.assertLess(post.index("verify_post_state"), post.index('echo "atomic Kaigo'))

    def test_cutover_removes_all_old_bridge_hooks_and_generations_atomically(self):
        source = _body()
        transaction_start = source.index('transaction="$(mktemp)"')
        transaction_end = source.index('"${RESTORE[@]}" < "${transaction}"')
        transaction = source[transaction_start:transaction_end]
        hook_parser = _function_body(source, "is_kaigo_egress_hook")
        self.assertNotIn('"${input_interface}" == "${BRIDGE}"', hook_parser)
        self.assertIn("KAIGO-BLD-FWD-*|KAIGO-BLD-HOST-*", hook_parser)
        self.assertIn('-j|-g) jump=', hook_parser)
        self.assertIn('is_kaigo_egress_hook "${rule}" DOCKER-USER', transaction)
        self.assertIn('is_kaigo_egress_hook "${rule}" INPUT', transaction)
        self.assertIn('echo "-I DOCKER-USER 1', transaction)
        self.assertIn('echo "-I INPUT 1', transaction)
        self.assertIn('echo "-F ${chain}"', transaction)
        self.assertIn('echo "-X ${chain}"', transaction)
        self.assertIn("echo 'COMMIT'", transaction)


def _can_execute_root_bash() -> bool:
    if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False
    bash = shutil.which("bash")
    if bash is None:
        return False
    result = subprocess.run(
        [bash, "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


@unittest.skipUnless(_can_execute_root_bash(), "requires root and a working Bash")
class BuilderEgressGuardStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("iptables", "iptables-save", "iptables-restore"):
            tool = self.bin / name
            tool.write_text(FAKE_XTABLES, encoding="utf-8")
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.state_path = self.root / "state.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def _g4_state(self) -> dict:
        forward = "KAIGO-BLD-FWD-G4"
        host = "KAIGO-BLD-HOST-G4"
        rules = [
            f"-A DOCKER-USER -i br-kaigo-build -j {forward}",
            "-A DOCKER-USER -j RETURN",
            f"-A INPUT -i br-kaigo-build -j {host}",
            "-A INPUT -p tcp -m tcp --dport 22 -j ACCEPT",
        ]
        rules.extend(
            f"-A {forward} -s 172.30.240.0/28 -d {network} "
            "-m conntrack --ctstate NEW -j REJECT "
            "--reject-with icmp-port-unreachable"
            for network in PRIVATE_DESTINATIONS
        )
        rules.extend(
            [
                f"-A {forward} -j RETURN",
                f"-A {host} -s 172.30.240.0/28 -m conntrack --ctstate NEW "
                "-j REJECT --reject-with icmp-port-unreachable",
                f"-A {host} -j RETURN",
            ]
        )
        return {
            "chains": {
                "INPUT": "ACCEPT",
                "DOCKER-USER": "-",
                forward: "-",
                host: "-",
            },
            "rules": rules,
        }

    def _write_state(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def _read_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _run(self, **environment: str | None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env["FAKE_IPTABLES_STATE"] = str(self.state_path)
        env["KAIGO_WORKER_BUILD_CLIENT_ADDRESS"] = "172.30.240.2"
        env["KAIGO_WORKER_PUBLIC_HTTPS_HOST"] = "5.129.236.90/32"
        env["KAIGO_WORKER_PUBLIC_HTTPS_PORT"] = "443"
        for name, value in environment.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_missing_or_invalid_public_https_contract_fails_before_mutation(self):
        original = self._g4_state()
        invalid_environments = (
            {"KAIGO_WORKER_BUILD_CLIENT_ADDRESS": None},
            {"KAIGO_WORKER_BUILD_CLIENT_ADDRESS": "172.30.240.2/32"},
            {"KAIGO_WORKER_BUILD_CLIENT_ADDRESS": "172.30.240.999"},
            {"KAIGO_WORKER_PUBLIC_HTTPS_HOST": None},
            {"KAIGO_WORKER_PUBLIC_HTTPS_HOST": "5.129.236.90"},
            {"KAIGO_WORKER_PUBLIC_HTTPS_HOST": "5.129.236.999/32"},
            {"KAIGO_WORKER_PUBLIC_HTTPS_PORT": None},
            {"KAIGO_WORKER_PUBLIC_HTTPS_PORT": "443/tcp"},
            {"KAIGO_WORKER_PUBLIC_HTTPS_PORT": "0"},
            {"KAIGO_WORKER_PUBLIC_HTTPS_PORT": "65536"},
        )
        for environment in invalid_environments:
            with self.subTest(environment=environment):
                self._write_state(original)
                result = self._run(**environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self._read_state(), original)

    def test_fresh_g4_migration_is_exact_and_idempotent(self):
        original = self._g4_state()
        self._write_state(original)

        first = self._run()
        self.assertEqual(first.returncode, 0, first.stderr)
        migrated = self._read_state()
        self.assertNotIn("KAIGO-BLD-FWD-G4", migrated["chains"])
        self.assertNotIn("KAIGO-BLD-HOST-G4", migrated["chains"])
        self.assertIn("KAIGO-BLD-FWD-G5", migrated["chains"])
        self.assertIn("KAIGO-BLD-HOST-G5", migrated["chains"])
        self.assertEqual(
            next(rule for rule in migrated["rules"] if rule.startswith("-A DOCKER-USER ")),
            "-A DOCKER-USER -i br-kaigo-build -j KAIGO-BLD-FWD-G5",
        )
        self.assertEqual(
            next(rule for rule in migrated["rules"] if rule.startswith("-A INPUT ")),
            "-A INPUT -i br-kaigo-build -j KAIGO-BLD-HOST-G5",
        )
        host_rules = [
            rule for rule in migrated["rules"]
            if rule.startswith("-A KAIGO-BLD-HOST-G5 ")
        ]
        self.assertEqual(
            host_rules,
            [
                "-A KAIGO-BLD-HOST-G5 -s 172.30.240.0/28 -d 172.19.0.1/32 "
                "-i br-kaigo-build -p tcp -m tcp --dport 8787 -m conntrack "
                "--ctstate NEW,ESTABLISHED -j ACCEPT",
                "-A KAIGO-BLD-HOST-G5 -s 172.30.240.2/32 -d 5.129.236.90/32 "
                "-i br-kaigo-build -p tcp -m tcp --dport 443 -m conntrack "
                "--ctstate NEW,ESTABLISHED -j ACCEPT",
                "-A KAIGO-BLD-HOST-G5 -s 172.30.240.0/28 -m conntrack "
                "--ctstate NEW -j REJECT --reject-with icmp-port-unreachable",
                "-A KAIGO-BLD-HOST-G5 -j RETURN",
            ],
        )
        g5_accepts = [
            rule
            for rule in migrated["rules"]
            if rule.startswith(("-A KAIGO-BLD-FWD-G5 ", "-A KAIGO-BLD-HOST-G5 "))
            and rule.endswith("-j ACCEPT")
        ]
        self.assertEqual(g5_accepts, host_rules[:2])
        self.assertIn(
            "-A KAIGO-BLD-FWD-G5 -s 172.30.240.0/28 -d 172.16.0.0/12 "
            "-m conntrack --ctstate NEW -j REJECT "
            "--reject-with icmp-port-unreachable",
            migrated["rules"],
        )
        self.assertIn("-A DOCKER-USER -j RETURN", migrated["rules"])
        self.assertIn(
            "-A INPUT -p tcp -m tcp --dport 22 -j ACCEPT", migrated["rules"]
        )

        second = self._run()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self._read_state(), migrated)

    def test_fresh_install_removes_all_egress_hooks_old_generations_and_legacy_source_rules(self):
        state = self._g4_state()
        state["chains"]["KAIGO-BLD-FWD-G2"] = "-"
        state["chains"]["KAIGO-BLD-HOST-G2"] = "-"
        state["chains"]["KAIGO-BUILDER-EGRESS"] = "-"
        state["chains"]["KAIGO-BUILDER-HOST"] = "-"
        state["rules"][1:1] = [
            "-A DOCKER-USER -j KAIGO-BLD-FWD-G4",
            "-A DOCKER-USER -i br-other -g KAIGO-BLD-FWD-G2",
        ]
        state["rules"][5:5] = [
            "-A INPUT -g KAIGO-BLD-HOST-G4",
            "-A INPUT -i br-other -j KAIGO-BUILDER-HOST",
        ]
        legacy_source = "172.30.240.2/32"
        state["rules"].extend(
            f"-A DOCKER-USER -s {legacy_source} -d {network} -j REJECT "
            "--reject-with icmp-port-unreachable"
            for network in LEGACY_DESTINATIONS
        )
        partial_lookalike = (
            "-A DOCKER-USER -s 172.30.240.3/32 -d 10.0.0.0/8 -j REJECT "
            "--reject-with icmp-port-unreachable"
        )
        state["rules"].append(partial_lookalike)
        self._write_state(state)

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        migrated = self._read_state()
        self.assertFalse(
            any(
                (
                    chain.startswith("KAIGO-BLD-FWD-")
                    and chain != "KAIGO-BLD-FWD-G5"
                )
                or (
                    chain.startswith("KAIGO-BLD-HOST-")
                    and chain != "KAIGO-BLD-HOST-G5"
                )
                or chain in {"KAIGO-BUILDER-EGRESS", "KAIGO-BUILDER-HOST"}
                for chain in migrated["chains"]
            )
        )
        self.assertFalse(
            any(
                rule.startswith(f"-A DOCKER-USER -s {legacy_source} ")
                for rule in migrated["rules"]
            )
        )
        self.assertIn(partial_lookalike, migrated["rules"])
        egress_hooks = [
            rule
            for rule in migrated["rules"]
            if "-j KAIGO-" in rule or "-g KAIGO-" in rule
        ]
        self.assertEqual(
            egress_hooks,
            [
                "-A DOCKER-USER -i br-kaigo-build -j KAIGO-BLD-FWD-G5",
                "-A INPUT -i br-kaigo-build -j KAIGO-BLD-HOST-G5",
            ],
        )

    def test_existing_exact_g5_with_legacy_source_signature_fails_without_mutation(self):
        self._write_state(self._g4_state())
        self.assertEqual(self._run().returncode, 0)
        dirty = self._read_state()
        legacy_source = "172.30.240.2/32"
        dirty["rules"].extend(
            f"-A DOCKER-USER -s {legacy_source} -d {network} -j REJECT "
            "--reject-with icmp-port-unreachable"
            for network in LEGACY_DESTINATIONS
        )
        self._write_state(dirty)

        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._read_state(), dirty)

    def test_partial_or_mutated_g5_is_rejected_without_state_change(self):
        partial = self._g4_state()
        partial["chains"]["KAIGO-BLD-FWD-G5"] = "-"
        for state in (partial, self._mutated_g5_state()):
            with self.subTest(chains=list(state["chains"])):
                self._write_state(state)
                before = self._read_state()
                result = self._run()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self._read_state(), before)

    def _mutated_g5_state(self) -> dict:
        state = self._g4_state()
        state["chains"]["KAIGO-BLD-FWD-G5"] = "-"
        state["chains"]["KAIGO-BLD-HOST-G5"] = "-"
        state["rules"].append("-A KAIGO-BLD-FWD-G5 -j ACCEPT")
        state["rules"].append("-A KAIGO-BLD-HOST-G5 -j RETURN")
        return state

    def test_extra_current_generation_hooks_are_rejected_without_repair(self):
        self._write_state(self._g4_state())
        self.assertEqual(self._run().returncode, 0)
        clean = self._read_state()
        for extra in (
            "-A DOCKER-USER -j KAIGO-BLD-FWD-G5",
            "-A INPUT -i br-other -j KAIGO-BLD-HOST-G5",
            "-A INPUT -g KAIGO-BLD-HOST-G5",
        ):
            with self.subTest(extra=extra):
                dirty = json.loads(json.dumps(clean))
                dirty["rules"].append(extra)
                self._write_state(dirty)
                result = self._run()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self._read_state(), dirty)

    def test_existing_g5_referenced_from_any_other_chain_fails_without_mutation(self):
        self._write_state(self._g4_state())
        self.assertEqual(self._run().returncode, 0)
        dirty = self._read_state()
        dirty["chains"]["FOREIGN"] = "-"
        dirty["rules"].append("-A FOREIGN -j KAIGO-BLD-FWD-G5")
        self._write_state(dirty)

        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._read_state(), dirty)

    def test_chain_name_race_fails_without_flushing_or_cutting_over(self):
        original = self._g4_state()
        self._write_state(original)
        raced = json.loads(json.dumps(original))
        raced["chains"]["FOREIGN"] = "-"
        raced["chains"]["KAIGO-BLD-FWD-G5"] = "-"
        raced["chains"]["KAIGO-BLD-HOST-G5"] = "-"
        raced["rules"].extend(
            [
                "-A FOREIGN -j KAIGO-BLD-FWD-G5",
                "-A KAIGO-BLD-FWD-G5 -j RETURN",
                "-A KAIGO-BLD-HOST-G5 -j RETURN",
            ]
        )

        result = self._run(FAKE_CREATE_G5_BEFORE_RESTORE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._read_state(), raced)

    def test_restore_failure_keeps_the_previous_generation_and_hooks(self):
        original = self._g4_state()
        self._write_state(original)
        result = self._run(FAKE_RESTORE_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._read_state(), original)


if __name__ == "__main__":
    unittest.main()
