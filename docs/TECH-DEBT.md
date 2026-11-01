# 技术债台账

一行一条，格式：`TODO(tech-debt, vNext): <文件:行号> <现象> <影响> <建议>`。
推送前新增的遗留项也记这里；修掉即删行。

- TODO(tech-debt, vNext): utils/candidate_pool.py:append `est_cost_s` 恒为 None，审批工具实现前无法按预估耗时给候选排序。影响：审批时只能按值 alphabet 序看。建议：阶段 3 审批工具落地时按维度历史耗时均值回填。
- TODO(tech-debt, vNext): utils/discover.py:_JS_HOTEL_GROUP 叶子节点提取对深层嵌套选项组（如品牌、位置「更多」展开区）可能把容器文案误收为值。影响：候选池可能出现少量低质候选。建议：L2 全集跑起来后按站点抽查 summary，必要时给该提取器加排除选择器。
- TODO(tech-debt, vNext): suites/e2e/tests/test_search_chain.py:_discover_quietly 与 suites/e2e/tests/test_merged_login_state.py:_discover_quietly 重复实现。影响：两处口径日后可能漂移。建议：阶段 2 抽到 utils/discover.py 提供统一 helper。
- TODO(tech-debt, vNext): utils/discover.py:HINTS 车站的 depart_station_opt/arrive_station_opt 值随线路变化，PER_DIMENSION_LIMIT=3 会让多站城市需多轮才能收全。影响：候选吸纳偏慢（非正确性问题）。建议：审批转正量上来后按维度放宽该维度阈值。
