| 变异体 | 变异内容 | 目标用例 | 退出码 | 判定 |
|---|---|---|---|---|
| M1 | 点击坐标 off-by-one：(x-1)*cell_width | `test_single_click_is_one_based_not_zero_based` | 1 | 变红 OK |
| M2 | 批量分支只点 tied[0]（不是全量批量） | `test_certain_safe_batch_clicks_all_tied_cells` | 1 | 变红 OK |
| M3 | 热力图契约退化成扁平短键 | `test_heatmap_contract_is_last_round_of_loop` | 1 | 变红 OK |
| M4 | 热力图只保留首轮（第二轮不覆盖） | `test_heatmap_contract_is_last_round_of_loop` | 1 | 变红 OK |
| M5 | 帮助模式漏掉 appended_pos 去重 | `test_help_mode_single_decision_no_click_and_dedup` | 1 | 变红 OK |
| M6 | 帮助模式被错误送进自动点击分支 | `test_help_mode_single_decision_no_click_and_dedup` | 1 | 变红 OK |
| M7 | 无进展守卫删除 | `test_loop_breaks_when_rescan_shows_no_progress` | 1 | 变红 OK |
| M8 | 连续循环退化为「一轮即返回」 | `test_heatmap_contract_is_last_round_of_loop` | 1 | 变红 OK |
| M9 | 循环内第二轮 decide 参数漂移 | `test_heatmap_contract_is_last_round_of_loop` | 1 | 变红 OK |
| M10 | 帮助模式预算乘数失效（恒 30s） | `test_budget_multiplier_roundtrip` | 1 | 变红 OK |
| M11 | 点击后不 rescan（重扫被漏掉） | `test_certain_safe_batch_clicks_all_tied_cells` | 1 | 变红 OK |
| M12 | is_play 未透传（新 pyd 恒收到 True） | `test_native_decide_new_pyd_passes_is_play` | 1 | 变红 OK |
| M13 | 旧 pyd 回退静默（不打 WARNING） | `test_native_decide_old_pyd_fallback` | 1 | 变红 OK |
| M14 | 帮助模式不放宽 wr_threshold（少 +6） | `test_help_mode_passes_relaxed_params` | 1 | 变红 OK |
| BASELINE | 还原生产代码后重跑同一用例 | `test_heatmap_contract_is_last_round_of_loop` | 0 | 变绿 OK |

变异体检出 14 / 14；基线恢复绿 = True
