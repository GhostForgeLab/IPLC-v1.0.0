#!/usr/bin/env python3
import argparse
import datetime as dt
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

APP = "iplc-light"
TABLE_FAMILY = "ip"
TABLE_NAME = "iplc_light"
STATE_DIR = Path("/var/lib/iplc-light")
STATE_FILE = STATE_DIR / "rules.json"
CONFIG_DIR = Path("/etc/nftables.d")
CONFIG_FILE = CONFIG_DIR / "iplc-light.nft"
NFT_MAIN = Path("/etc/nftables.conf")
BACKUP_DIR = Path("/var/backups/iplc-light")

def die(msg, code=1):
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)

def run(cmd, check=True, capture=True, input_text=None):
    p = subprocess.run(
        cmd,
        text=True,
        input=input_text,
        capture_output=capture,
    )
    if check and p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip()
        die(f"命令执行失败：{' '.join(cmd)}" + (f"\n{detail}" if detail else ""))
    return p

def need_root():
    if os.geteuid() != 0:
        die("请使用 root 权限运行，例如：sudo iplc-list")

def ensure_dirs():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")

def load_state():
    ensure_dirs()
    if not STATE_FILE.exists():
        return {"version": 1, "rules": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"状态文件损坏：{STATE_FILE}：{e}")
    return validate_state(data)

def validate_port(value, label):
    try:
        p = int(value)
    except Exception:
        die(f"{label}必须是数字")
    if not (1 <= p <= 65535):
        die(f"{label}必须在 1-65535 之间")
    return p

def validate_ipv4(value):
    try:
        ip = ipaddress.ip_address(value)
    except Exception:
        die("目标 IP 格式不正确")
    if ip.version != 4:
        die("当前轻量版只管理 IPv4 DNAT")
    return str(ip)

def validate_state(data):
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("rules"), list):
        die("状态文件格式不正确：需要版本 1 和规则列表")
    seen = set()
    rules = []
    for rule in data["rules"]:
        if not isinstance(rule, dict):
            die("状态文件包含无效规则")
        lp = validate_port(rule.get("listen_port"), "入口端口")
        ip = validate_ipv4(rule.get("target_ip"))
        tp = validate_port(rule.get("target_port"), "目标端口")
        if lp in seen:
            die(f"状态文件包含重复入口端口：{lp}")
        seen.add(lp)
        normalized = dict(rule)
        normalized.update(listen_port=lp, target_ip=ip, target_port=tp)
        rules.append(normalized)
    return {**data, "rules": rules}

def render_config(state):
    lines = [
        "# Managed by IPLC Light. Do not edit manually.",
        "table ip iplc_light {",
        "    chain prerouting {",
        "        type nat hook prerouting priority dstnat; policy accept;",
    ]
    for r in sorted(state["rules"], key=lambda x: int(x["listen_port"])):
        lp, ip, tp = int(r["listen_port"]), r["target_ip"], int(r["target_port"])
        lines.append(f'        tcp dport {lp} dnat to {ip}:{tp} comment "iplc-light:{lp}:tcp"')
        lines.append(f'        udp dport {lp} dnat to {ip}:{tp} comment "iplc-light:{lp}:udp"')
    lines += [
        "    }",
        "",
        "    chain postrouting {",
        "        type nat hook postrouting priority srcnat; policy accept;",
    ]
    for r in sorted(state["rules"], key=lambda x: int(x["listen_port"])):
        lp, ip, tp = int(r["listen_port"]), r["target_ip"], int(r["target_port"])
        lines.append(f'        ip daddr {ip} tcp dport {tp} masquerade comment "iplc-light:{lp}:masq-tcp"')
        lines.append(f'        ip daddr {ip} udp dport {tp} masquerade comment "iplc-light:{lp}:masq-udp"')
    lines += [
        "    }",
        "}",
        "",
    ]
    return "\n".join(lines)

def table_exists():
    p = run(["nft", "list", "table", TABLE_FAMILY, TABLE_NAME], check=False)
    return p.returncode == 0

def check_and_apply_candidate(candidate):
    config = render_config(candidate)
    ensure_dirs()
    with tempfile.TemporaryDirectory(prefix="iplc-light-") as td:
        td = Path(td)
        cfg = td / "iplc-light.nft"
        txn = td / "transaction.nft"
        cfg.write_text(config, encoding="utf-8")
        if table_exists():
            txn.write_text(
                f"delete table {TABLE_FAMILY} {TABLE_NAME}\n" + config,
                encoding="utf-8"
            )
        else:
            txn.write_text(config, encoding="utf-8")

        chk = run(["nft", "-c", "-f", str(txn)], check=False)
        if chk.returncode != 0:
            detail = (chk.stderr or chk.stdout or "").strip()
            die("nftables 语法/事务检查失败，未应用任何修改。" + (f"\n{detail}" if detail else ""))

        app = run(["nft", "-f", str(txn)], check=False)
        if app.returncode != 0:
            detail = (app.stderr or app.stdout or "").strip()
            die("nftables 应用失败，状态文件未修改。" + (f"\n{detail}" if detail else ""))

        # Runtime apply succeeded; persist atomically.
        state_tmp = STATE_FILE.with_suffix(".json.tmp")
        config_tmp = CONFIG_FILE.with_suffix(".nft.tmp")
        state_tmp.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        config_tmp.write_text(config, encoding="utf-8")
        os.replace(state_tmp, STATE_FILE)
        os.replace(config_tmp, CONFIG_FILE)

def ensure_include():
    ensure_dirs()
    if not NFT_MAIN.exists():
        NFT_MAIN.write_text("#!/usr/sbin/nft -f\n\n", encoding="utf-8")
    text = NFT_MAIN.read_text(encoding="utf-8", errors="replace")
    if has_active_include(text):
        return
    if text and not text.endswith("\n"):
        text += "\n"
    text += '\n# IPLC Light\ninclude "/etc/nftables.d/iplc-light.nft"\n'
    tmp = NFT_MAIN.with_suffix(".conf.iplc.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, NFT_MAIN)

def nft_json():
    p = run(["nft", "-j", "list", "ruleset"], check=False)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except Exception:
        return None

def parse_dnat_rules(data=None):
    if data is None:
        data = nft_json()
    if not data:
        return []
    out = []
    for item in data.get("nftables", []):
        rr = item.get("rule")
        if not rr:
            continue
        proto = None
        dport = None
        target_ip = None
        target_port = None
        for expr in rr.get("expr", []):
            m = expr.get("match")
            if m and m.get("op") == "==":
                left, right = m.get("left"), m.get("right")
                if isinstance(left, dict):
                    pl = left.get("payload")
                    if isinstance(pl, dict) and pl.get("field") == "dport":
                        proto = pl.get("protocol")
                        if isinstance(right, int):
                            dport = right
            dn = expr.get("dnat")
            if isinstance(dn, dict):
                target_ip = dn.get("addr")
                target_port = dn.get("port")
        if dport is not None and target_ip:
            out.append({
                "family": rr.get("family"),
                "table": rr.get("table"),
                "chain": rr.get("chain"),
                "handle": rr.get("handle"),
                "proto": proto or "?",
                "listen_port": int(dport),
                "target_ip": str(target_ip),
                "target_port": int(target_port) if isinstance(target_port, int) else target_port,
                "comment": rr.get("comment", ""),
                "managed": rr.get("family") == TABLE_FAMILY and rr.get("table") == TABLE_NAME,
            })
    return out

def backup(reason="manual"):
    need_root()
    ensure_dirs()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    work = Path(tempfile.mkdtemp(prefix="iplc-backup-"))
    try:
        (work / "metadata.json").write_text(json.dumps({
            "created_at": now_iso(),
            "reason": reason,
            "hostname": os.uname().nodename,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if STATE_FILE.exists():
            shutil.copy2(STATE_FILE, work / "state.json")
        else:
            (work / "state.json").write_text(json.dumps({"version": 1, "rules": []}, indent=2) + "\n")
        if CONFIG_FILE.exists():
            shutil.copy2(CONFIG_FILE, work / "iplc-light.nft")
        if NFT_MAIN.exists():
            shutil.copy2(NFT_MAIN, work / "nftables.conf")
        p = run(["nft", "list", "ruleset"], check=False)
        (work / "ruleset.nft").write_text(p.stdout or "", encoding="utf-8")
        p2 = run(["sysctl", "-n", "net.ipv4.ip_forward"], check=False)
        (work / "ip_forward.txt").write_text((p2.stdout or "").strip() + "\n", encoding="utf-8")
        base = BACKUP_DIR / f"{stamp}_{reason}"
        archive = shutil.make_archive(str(base), "gztar", root_dir=work)
        return archive
    finally:
        shutil.rmtree(work, ignore_errors=True)

def list_cmd(args=None):
    need_root()
    state = load_state()
    dnat = parse_dnat_rules()
    managed_state = {int(r["listen_port"]): r for r in state["rules"]}

    print("=== IPLC 转发规则 ===")
    if not dnat and not managed_state:
        print("当前未发现 DNAT 转发规则。")
        return

    grouped = {}
    for r in dnat:
        key = (r["listen_port"], str(r["target_ip"]), str(r["target_port"]), r["table"])
        grouped.setdefault(key, set()).add(r["proto"])

    rows = []
    seen_managed = set()
    for (lp, ip, tp, table), protos in sorted(grouped.items(), key=lambda x: x[0][0]):
        is_managed = table == TABLE_NAME
        tag = "脚本管理" if is_managed else "原有/外部"
        if is_managed:
            seen_managed.add(lp)
        rows.append((lp, ",".join(sorted(protos)), ip, tp, tag, table))

    # State exists but runtime missing.
    for lp, r in sorted(managed_state.items()):
        if lp not in seen_managed:
            rows.append((lp, "-", r["target_ip"], r["target_port"], "状态存在/运行缺失", TABLE_NAME))

    print(f"{'入口端口':<10} {'协议':<10} {'目标':<30} {'归属':<14} {'表'}")
    print("-" * 86)
    for lp, protos, ip, tp, tag, table in rows:
        print(f"{lp:<10} {protos:<10} {str(ip)+':'+str(tp):<30} {tag:<14} {table}")
    print()
    print("说明：删除命令只删除“脚本管理”的规则，默认不会碰“原有/外部”生产规则。")

def external_conflicts(port):
    data = nft_json()
    if not isinstance(data, dict) or not isinstance(data.get("nftables"), list):
        die("无法读取当前 nftables 规则，为避免冲突已拒绝操作")

    known = [r for r in parse_dnat_rules(data)
             if r["listen_port"] == int(port) and not r["managed"]]
    for item in data["nftables"]:
        rr = item.get("rule")
        if not isinstance(rr, dict):
            continue
        if rr.get("family") not in ("ip", "inet") or (rr.get("family"), rr.get("table")) == (TABLE_FAMILY, TABLE_NAME):
            continue
        exprs = rr.get("expr", [])
        if not isinstance(exprs, list) or not any(isinstance(e, dict) and ("dnat" in e or "redirect" in e) for e in exprs):
            continue
        dports = []
        for expr in exprs:
            if not isinstance(expr, dict):
                continue
            match = expr.get("match", {})
            if not isinstance(match, dict):
                continue
            left = match.get("left", {})
            payload = left.get("payload", {}) if isinstance(left, dict) else {}
            if isinstance(payload, dict) and payload.get("field") == "dport":
                dports.append(match)
        if len(dports) != 1 or dports[0].get("op") != "==" or type(dports[0].get("right")) is not int:
            die("发现无法确定入口端口的外部 DNAT/redirect（可能使用集合或映射），为保护现有转发已拒绝操作")
        if dports[0]["right"] == int(port) and not known:
            die(f"入口端口 {port} 已被外部 NAT 规则占用，已拒绝操作")
    return known

def add_cmd(args):
    need_root()
    state = load_state()

    if args.listen_port is None:
        lp = validate_port(input("入口端口：").strip(), "入口端口")
    else:
        lp = validate_port(args.listen_port, "入口端口")

    if args.target_ip is None:
        ip = validate_ipv4(input("目标 IP：").strip())
    else:
        ip = validate_ipv4(args.target_ip)

    if args.target_port is None:
        tp = validate_port(input("目标端口：").strip(), "目标端口")
    else:
        tp = validate_port(args.target_port, "目标端口")

    for r in state["rules"]:
        if int(r["listen_port"]) == lp:
            die(f"入口端口 {lp} 已由 IPLC Light 管理：{r['target_ip']}:{r['target_port']}")

    conflicts = external_conflicts(lp)
    if conflicts:
        desc = "; ".join(f"{r['proto']} {r['table']}/{r['chain']} -> {r['target_ip']}:{r['target_port']}" for r in conflicts)
        die(f"入口端口 {lp} 已被现有 nftables DNAT 使用，为避免破坏生产规则已阻止添加。\n{desc}")

    print("\n即将添加：")
    print(f"  TCP/UDP {lp} -> {ip}:{tp}")
    if not args.yes:
        ans = input("确认添加？输入 y 继续：").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消，未修改任何规则。")
            return

    b = backup(f"before-add-{lp}")
    candidate = json.loads(json.dumps(state))
    candidate["rules"].append({
        "listen_port": lp,
        "target_ip": ip,
        "target_port": tp,
        "created_at": now_iso(),
    })
    check_and_apply_candidate(candidate)
    print(f"添加成功：{lp} TCP/UDP -> {ip}:{tp}")
    print(f"自动备份：{b}")

def del_cmd(args):
    need_root()
    state = load_state()
    if args.listen_port is None:
        lp = validate_port(input("要删除的入口端口：").strip(), "入口端口")
    else:
        lp = validate_port(args.listen_port, "入口端口")

    hit = None
    for r in state["rules"]:
        if int(r["listen_port"]) == lp:
            hit = r
            break

    if hit is None:
        conflicts = external_conflicts(lp)
        if conflicts:
            desc = "; ".join(f"{r['proto']} {r['table']}/{r['chain']} -> {r['target_ip']}:{r['target_port']}" for r in conflicts)
            die(f"{lp} 属于原有/外部规则，不由 IPLC Light 管理，已拒绝删除以保护生产规则。\n{desc}")
        die(f"未找到入口端口 {lp} 的脚本管理规则")

    print(f"\n即将删除：TCP/UDP {lp} -> {hit['target_ip']}:{hit['target_port']}")
    if not args.yes:
        ans = input("确认删除？输入 y 继续：").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消，未修改任何规则。")
            return

    b = backup(f"before-del-{lp}")
    candidate = json.loads(json.dumps(state))
    candidate["rules"] = [r for r in candidate["rules"] if int(r["listen_port"]) != lp]
    check_and_apply_candidate(candidate)
    print(f"删除成功：{lp}")
    print(f"自动备份：{b}")

def backup_cmd(args=None):
    b = backup("manual")
    print(f"备份完成：{b}")

def restore_cmd(args):
    need_root()
    ensure_dirs()
    backups = sorted(BACKUP_DIR.glob("*.tar.gz"), reverse=True)
    if not backups and not args.archive:
        die("没有可用备份")

    if args.archive:
        arc = Path(args.archive)
        if not arc.exists():
            die(f"备份文件不存在：{arc}")
    else:
        print("最近备份：")
        for i, p in enumerate(backups[:10], 1):
            print(f"  {i}. {p.name}")
        raw = input("选择序号（默认 1）：").strip() or "1"
        try:
            idx = int(raw)
        except ValueError:
            die("选择无效")
        if not 1 <= idx <= min(10, len(backups)):
            die("选择无效")
        arc = backups[idx - 1]

    try:
        with tarfile.open(arc, "r:gz") as tf:
            members = [m for m in tf.getmembers() if m.name == "state.json"]
            if len(members) != 1 or not members[0].isfile() or members[0].size > 10 * 1024 * 1024:
                die("备份包缺少有效的 state.json，无法安全恢复")
            with tf.extractfile(members[0]) as state_stream:
                candidate = validate_state(json.load(state_stream))
    except (OSError, tarfile.TarError, ValueError, UnicodeError, json.JSONDecodeError) as e:
        die(f"备份包无法读取或状态文件损坏：{e}")

    print(f"将恢复脚本管理规则到备份：{arc.name}")
    if not args.yes:
        ans = input("确认恢复？输入 y 继续：").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消。")
            return

    pre = backup("before-restore")
    check_and_apply_candidate(candidate)
    print("恢复完成。")
    print(f"恢复前自动备份：{pre}")
    print("说明：恢复只重建 IPLC Light 自己管理的规则，不覆盖整套系统 nftables。")

def has_active_include(text):
    pattern = re.compile(r'^\s*include\s+["\']?/etc/nftables\.d/(?:iplc-light\.nft|\*\.nft|\*)["\']?\s*(?:#.*)?$')
    return any(pattern.fullmatch(line) for line in text.splitlines())

def check_include_present():
    if not NFT_MAIN.exists():
        return False
    text = NFT_MAIN.read_text(encoding="utf-8", errors="replace")
    return has_active_include(text)

def selfcheck_cmd(args=None):
    need_root()
    state = load_state()
    fails, warns = [], []

    def ok(msg): print(f"[ OK ] {msg}")
    def fail(msg):
        print(f"[FAIL] {msg}")
        fails.append(msg)
    def warn(msg):
        print(f"[WARN] {msg}")
        warns.append(msg)

    print("=== IPLC Light 一键自检 ===")

    if shutil.which("nft"): ok("nft 命令存在")
    else: fail("nft 命令不存在")

    if shutil.which("python3"): ok("python3 存在")
    else: fail("python3 不存在")

    p = run(["sysctl", "-n", "net.ipv4.ip_forward"], check=False)
    if p.returncode == 0 and p.stdout.strip() == "1":
        ok("IPv4 Forward = 1")
    else:
        fail("IPv4 Forward 未开启")

    svc_enabled = run(["systemctl", "is-enabled", "nftables"], check=False)
    if svc_enabled.returncode == 0 and svc_enabled.stdout.strip() in ("enabled", "static"):
        ok(f"nftables 开机状态：{svc_enabled.stdout.strip()}")
    else:
        warn("nftables 未显示为 enabled/static；重启后的持久化需要确认")

    svc_active = run(["systemctl", "is-active", "nftables"], check=False)
    if svc_active.returncode == 0 and svc_active.stdout.strip() == "active":
        ok("nftables 服务 active")
    else:
        warn("nftables 服务当前不是 active（运行时规则仍可能存在）")

    if check_include_present():
        ok("/etc/nftables.conf 已包含 IPLC Light 配置")
    else:
        fail("/etc/nftables.conf 未包含 IPLC Light 配置")

    if STATE_FILE.exists():
        ok(f"状态文件存在：{STATE_FILE}")
    else:
        fail("状态文件不存在")

    if CONFIG_FILE.exists():
        ok(f"持久化配置存在：{CONFIG_FILE}")
    else:
        fail("持久化配置不存在")

    # Candidate nft transaction syntax check.
    config = render_config(state)
    with tempfile.TemporaryDirectory(prefix="iplc-check-") as td:
        txn = Path(td) / "check.nft"
        if table_exists():
            txn.write_text(f"delete table {TABLE_FAMILY} {TABLE_NAME}\n" + config, encoding="utf-8")
        else:
            txn.write_text(config, encoding="utf-8")
        p = run(["nft", "-c", "-f", str(txn)], check=False)
        if p.returncode == 0:
            ok("nftables 配置事务检查通过")
        else:
            fail("nftables 配置事务检查失败：" + ((p.stderr or p.stdout or "").strip()))

    if table_exists():
        ok(f"运行时表存在：{TABLE_FAMILY} {TABLE_NAME}")
    else:
        fail(f"运行时表不存在：{TABLE_FAMILY} {TABLE_NAME}")

    dnat = parse_dnat_rules()
    managed_runtime = [r for r in dnat if r["managed"]]
    expected = len(state["rules"]) * 2
    if len(managed_runtime) == expected:
        ok(f"运行时 DNAT 数量匹配：{len(managed_runtime)}")
    else:
        fail(f"运行时 DNAT 数量不匹配：实际 {len(managed_runtime)}，应为 {expected}")

    external = [r for r in dnat if not r["managed"]]
    ext_ports = {}
    for r in external:
        ext_ports.setdefault(r["listen_port"], []).append(r)
    collisions = []
    for r in state["rules"]:
        lp = int(r["listen_port"])
        if lp in ext_ports:
            collisions.append(lp)
    if collisions:
        fail("发现脚本规则与外部 DNAT 入口端口冲突：" + ",".join(map(str, sorted(set(collisions)))))
    else:
        ok("未发现脚本规则与外部 DNAT 入口端口冲突")

    for r in state["rules"]:
        p = run(["ip", "route", "get", r["target_ip"]], check=False)
        if p.returncode == 0:
            ok(f"目标路由可解析：{r['target_ip']}")
        else:
            fail(f"目标路由不可解析：{r['target_ip']}")

    try:
        test = BACKUP_DIR / ".write-test"
        test.write_text("ok\n")
        test.unlink()
        ok(f"备份目录可写：{BACKUP_DIR}")
    except Exception as e:
        fail(f"备份目录不可写：{e}")

    print()
    print(f"自检结果：{len(fails)} 个失败，{len(warns)} 个警告。")
    if fails:
        print("结论：存在需要处理的问题。")
        raise SystemExit(2)
    elif warns:
        print("结论：核心检查通过，但有警告项。")
    else:
        print("结论：全部通过。")

def init_cmd(args=None):
    need_root()
    ensure_dirs()
    if not STATE_FILE.exists():
        if table_exists() or CONFIG_FILE.exists():
            die("状态文件缺失，但检测到运行时表或持久化配置；已停止初始化以保护现有转发。请先从备份恢复规则状态。")
    ensure_include()
    if not STATE_FILE.exists():
        STATE_FILE.write_text(json.dumps({"version": 1, "rules": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state = load_state()
    # Always render/apply only our own table; external rules untouched.
    check_and_apply_candidate(state)
    print("IPLC Light 初始化完成。")

def main():
    prog = Path(sys.argv[0]).name
    aliases = {
        "iplc-list": "list",
        "iplc-add": "add",
        "iplc-del": "del",
        "iplc-backup": "backup",
        "iplc-restore": "restore",
        "iplc-check": "check",
    }

    if prog in aliases:
        cmd = aliases[prog]
        argv = [cmd] + sys.argv[1:]
    else:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description="IPLC Light - nftables 轻量转发管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="查看当前转发规则")

    p_add = sub.add_parser("add", help="添加 TCP/UDP 转发")
    p_add.add_argument("listen_port", nargs="?")
    p_add.add_argument("target_ip", nargs="?")
    p_add.add_argument("target_port", nargs="?")
    p_add.add_argument("-y", "--yes", action="store_true")

    p_del = sub.add_parser("del", help="删除脚本管理规则")
    p_del.add_argument("listen_port", nargs="?")
    p_del.add_argument("-y", "--yes", action="store_true")

    sub.add_parser("backup", help="备份当前 nftables 与脚本状态")

    p_res = sub.add_parser("restore", help="恢复脚本管理规则")
    p_res.add_argument("archive", nargs="?")
    p_res.add_argument("-y", "--yes", action="store_true")

    sub.add_parser("check", help="一键自检")
    sub.add_parser("init", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if args.cmd == "list": list_cmd(args)
    elif args.cmd == "add": add_cmd(args)
    elif args.cmd == "del": del_cmd(args)
    elif args.cmd == "backup": backup_cmd(args)
    elif args.cmd == "restore": restore_cmd(args)
    elif args.cmd == "check": selfcheck_cmd(args)
    elif args.cmd == "init": init_cmd(args)

if __name__ == "__main__":
    main()
