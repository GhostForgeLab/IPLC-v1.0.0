# IPLC Light

基于 **Debian + nftables** 的轻量级 IPLC 端口转发管理脚本。

主要用于在 IPLC 中转 VPS 上快速添加、删除、查看和备份 TCP/UDP DNAT 转发规则。

## 功能

* 查看当前 DNAT 转发规则
* 添加 TCP + UDP 转发
* 删除脚本管理的转发规则
* 自动配置 masquerade
* 添加/删除前自动备份
* 端口冲突检测
* nftables 配置语法检查
* 配置持久化
* 备份与恢复
* 一键自检
* 默认保护原有/外部 nftables 规则

---

# 一、新 VPS 安装
## 在线一键安装

适用于全新的 Debian VPS。

使用 root 执行：

```bash
bash -c 'set -e; command -v curl >/dev/null 2>&1 || { apt-get update && apt-get install -y curl; }; d=$(mktemp -d); curl -fL https://github.com/GhostForgeLab/IPLC-v1.0.0/releases/latest/download/iplc-light.tar.gz -o "$d/iplc-light.tar.gz"; tar -xzf "$d/iplc-light.tar.gz" -C "$d"; bash "$d/iplc-light/install.sh"; rm -rf "$d"'
```

安装完成后运行：

```bash
iplc-check
```

查看当前转发规则：

```bash
iplc-list
```

以后新增转发：

```bash
iplc-add
```


离线安装：

`iplc-light.tar.gz`

上传到 VPS 的：

`/root`

目录。

然后执行：

```bash
cd /root
tar -xzf iplc-light.tar.gz
cd iplc-light
bash install.sh
```

安装完成后执行：

```bash
iplc-check
```

如果最后显示：

```text
自检结果：0 个失败，0 个警告。
结论：全部通过。
```

说明安装正常。

---

# 二、常用命令

## 1. 查看当前转发

```bash
iplc-list
```

用于查看服务器当前所有可识别的 DNAT 转发规则。

脚本自己创建的规则显示为：

```text
脚本管理
```

服务器原来已有的规则显示为：

```text
原有/外部
```

---

## 2. 添加转发

执行：

```bash
iplc-add
```

根据提示依次输入：

```text
入口端口：
目标 IP：
目标端口：
```

例如：

```text
入口端口：37185
目标 IP：1.2.3.4
目标端口：25884
```

脚本会自动创建：

```text
TCP 37185 → 1.2.3.4:25884
UDP 37185 → 1.2.3.4:25884
```

并自动配置对应的 masquerade。

也可以直接使用命令添加：

```bash
iplc-add 37185 1.2.3.4 25884 -y
```

---

## 3. 删除转发

执行：

```bash
iplc-del
```

然后输入需要删除的入口端口。

也可以直接：

```bash
iplc-del 37185 -y
```

注意：

`iplc-del` 默认只删除 IPLC Light 自己管理的规则。

对于标记为：

```text
原有/外部
```

的规则，脚本默认拒绝删除，防止误删生产线路。

---

## 4. 手动备份

```bash
iplc-backup
```

备份默认保存在：

```text
/var/backups/iplc-light/
```

另外，在添加、删除和恢复规则之前，脚本也会自动创建备份。

---

## 5. 恢复备份

```bash
iplc-restore
```

脚本会显示最近的备份，根据提示选择需要恢复的版本即可。

恢复只针对 IPLC Light 自己管理的规则，不会直接覆盖服务器整套 nftables 配置。

---

## 6. 一键检查

```bash
iplc-check
```

会统一检查：

* nftables
* Python 3
* IPv4 Forward
* nftables 服务状态
* 配置持久化
* 状态文件
* nftables 配置语法
* IPLC Light 运行表
* DNAT 规则数量
* 端口冲突
* 目标 IP 路由
* 备份目录

正常状态：

```text
自检结果：0 个失败，0 个警告。
结论：全部通过。
```

---

# 三、以后更换新的 IPLC VPS

新 VPS 安装流程仍然是：

```bash
cd /root
tar -xzf iplc-light.tar.gz
cd iplc-light
bash install.sh
iplc-check
```

然后使用：

```bash
iplc-add
```

重新添加需要的 IPLC 转发线路即可。

注意：

**安装包包含的是 IPLC Light 管理工具本身，不包含旧 VPS 当前正在运行的生产转发规则。**

因此，更换新的 IPLC VPS 后，需要根据实际线路重新添加目标 IP、入口端口和目标端口。

---

# 四、文件位置

程序：

```text
/usr/local/lib/iplc-light/
```

常用命令：

```text
/usr/local/bin/
```

状态文件：

```text
/var/lib/iplc-light/rules.json
```

nftables 持久化配置：

```text
/etc/nftables.d/iplc-light.nft
```

备份：

```text
/var/backups/iplc-light/
```

---

# 五、常用命令速查

```bash
# 查看规则
iplc-list

# 添加规则
iplc-add

# 删除规则
iplc-del

# 手动备份
iplc-backup

# 恢复备份
iplc-restore

# 一键自检
iplc-check
```

## 环境

已验证：
- Debian 13

主要依赖：
- nftables
- Python 3
- systemd
- iproute2

理论兼容 Debian 12 及后续 Debian 版本。
系统大版本升级后建议运行 `iplc-check` 进行兼容性检查。

版本：

`IPLC Light v1.0.0`
