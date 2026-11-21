# 澄川计量介质交换 V1

设备班把定宽源卷和封定目录交给白班。资料员按夜班给定的原位清单顺序转排一份交换卷。夜班导入员凭正式号复领签发时的字节，质检员按目标序号查原件和固定核账。领取只需要成果库。

## 运行

在仓库根目录使用 Python 3.11，依赖均为标准库。控制台提供完整回归入口。

```text
python3 -B check_project.py
python3 -B -m issue.cli demo
python3 -B -m issue.cli demo-dual
```

演示从 `examples/shift.json` 读取目录和五个物理帧，在临时工作目录交卷。源卷及两份目录移除后，新的领取进程导出顺读正文。选件 `[0,2,4]` 得到 `000`、三个空格、` 07`。账面是 3 条、2 条有效、1 条缺测、1 条实测零，读数总量为 7。

双卷演示从 `examples/dual_shift.json` 排程并实写两个交换卷，再用卸源后重开的成果库逐号复领。

## 白班和夜班命令

以下命令假设工作目录已备好登记描述 `registered.json`、装置独立提供的挂载描述 `mounted.json` 和无分隔符的 `source.bin`。领取时使用交卷回执中的 `number`。

```text
python3 -B -m issue.cli deliver --store issues.db --directory registered.json --mounted-directory mounted.json --source source.bin --positions '[0,2,4]'
python3 -B -m issue.cli retrieve --store issues.db ISS-000001 --output import.bin
python3 -B -m issue.cli deliver-dual --store issues.db \
    --directory left.json --directory right.json \
    --mounted-directory left-mounted.json --mounted-directory right-mounted.json \
    --source left.bin --source right.bin \
    --targets '[["meter",[["RIGHT",0],["RIGHT",1],["LEFT",0]]],["quality",[["RIGHT",1],["RIGHT",0],["LEFT",0]]]]' \
    --capacity 1
python3 -B -m issue.cli demo-dual
```

双卷命令按计划顺序各给两次目录和挂载描述；`--targets` 为按交卷次序排列的两个目标，容量为零时输出 `NO_PLAN` 且不建库。

描述是含 `volume_id`、`width`、`slot_count` 和有序 `positions` 的 JSON 对象，`version` 可省略，默认为 1。登记描述与挂载描述可以来自不同文件，启动时必须完整相同。`--output` 创建新的顺读原始字节文件。领取 JSON 也提供 `body_hex` 和 `width`。两条命令均输出正式号、按目标序号排列的出处及固定核账。JSON 的总量使用十进制字符串，Python 接口的总量为精确整数。业务或 IO 拒绝时命令向标准错误输出错误，退出码为 1。参数解析错误遵循 argparse 的退出语义。

## Python 接口

| 入口 | 合同 |
| --- | --- |
| `source.register_source(catalog, volume_id, width, slot_count, positions, *, version=1)` | 固定首份目录并返回卷号。等值登记复用卷号，非法布局或同号冲突报 `ValueError`。 |
| `plan.plan_single(catalog, volume_id, positions)` | 在目录上生成不可变的单卷计划。清单是严格递增的目录原位序列，可为空。未知卷报 `UnknownSource`，非法选件报 `InvalidSelection`。 |
| `media.execute_single(plan, device)` | 对照设备描述后推进一个只进读头，完成一份带实写出处的候选卷。描述不符报 `MountMismatch`，开始后的转排失败报 `ExecutionError` 并废弃已创建的候选。 |
| `issue.publish_single(catalog, plan, candidate)` | 按绑定、正文边界和逐条出处核账，原子提交并返回 `IssueReceipt`。核对或提交失败报 `PublishError` 并废弃本次候选。 |
| `issue.read_issue(catalog, number)` | 按正式号精确领取 `IssuedVolume`。未知号及候选号报 `IssueNotFound`。`record_at(i)` 和 `receipt.origin_at(i)` 使用从零起的目标序号。 |
| `plan.plan_transfer(directories, targets, capacity)` | 只看两份目录和两个目标清单，返回不可变的 `DualPlan`；容量下确实无解时返回字符串 `"NO_PLAN"`。非法目录、清单或容量报 `ValueError`。 |
| `media.execute_transfer(catalog, plan, devices, targets, rack, *, writers=None)` | 按计划推进两个只进读头，经留存架兑现共享原件，核对两卷后原子签发两个正式号，返回两个 `IssueReceipt`。执行失败报 `ExecutionError`，核对或提交失败报 `PublishError`；两种失败都废弃本次候选并释放本次留存。 |

`SourceCatalog` 持有工作会话的登记目录。`SourceDirectory` 固定源卷号、`RecordLayout`、物理槽数和可选原位。用独立挂载描述调用 `open_frame_device(path, directory)`，使用后调用 `close()`。转排的 `candidate_factory` 可装配顺序目标写头，默认在本次候选内顺写定宽字节。候选是一次办理中的对象，交卷由调用者在本次办理完成后提交。`IssueStore(path).initialize()` 初始化本地成果库，再用该对象构造 `IssueCatalog`。重复初始化保留全部已提交成果。

## 介质和核账

原位为零起的物理槽号。读头从槽 0 起逐帧经过未选位置，读到最后一个选件即停。所有经过的帧都校验完整宽度和格式。空选件在绑定通过后完成空候选，全程零正文读写。正文帧以 ASCII 空格左填充，可带一个负号，随后至少一位 ASCII 十进制数字。全空格帧代表缺测。相同字节的两个原位仍为不同原件。

目标卷直接保留完整原字节。写头确认整帧后才登记目标序号和实际具名原位。签发前逐条比较预定和实际出处，按实写定宽正文计算总条数、有效数、缺测数、实测零数与精确整数代数和。缺测只计条数及缺测，实测零同时计入有效记录和零值。核账单位为源记录的原整数单位。正文、出处和固定计量在同一事务内可见。失败的候选不能继续写入、封定或签发，已签卷仍按各自的正式号可领。
