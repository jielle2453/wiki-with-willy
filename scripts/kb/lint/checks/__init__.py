"""健檢的每一項（lint 題，檢查清單一～三類）。每個模組有 NAME（白話，出錯時報告寫「NAME這項這次沒查成」）與 run(ctx)（回 findings）。
CHECKS 照這個順序跑：會標綜合卡要更新的在前，todo（整理擱著的件數）最後，數字才把這次標的算進去。"""
from kb.lint.checks import (archive, backup, citations, empty_sections, guard, handedits, inferred, preferences,
                            rulings_promote, sizes, stale_by_profile, system_dir, tag_edits, todo, versions)

CHECKS = [citations, empty_sections, stale_by_profile, guard, handedits, tag_edits, inferred, versions, sizes, backup,
          system_dir, rulings_promote, preferences, archive, todo]
