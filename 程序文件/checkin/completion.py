"""Finalize confirmed tasks without turning cleanup errors into submit failures."""
from . import cleanup
from .storage import MATERIAL_FIELDS


def finalize_completed(history, root, row, report):
    if row.get('status') != '已完成':
        raise ValueError('未确认完成的材料不能清理。')
    completed = row
    try:
        completed = history.clear_completed_materials(row['id'])
    except (OSError, ValueError) as error:
        report('系统结果已记录，材料记录清理失败：' + str(error))
    try:
        cleanup.clear_record_photos(root, row['id'])
    except (OSError, ValueError) as error:
        report('系统结果已记录，旧配图清理失败：' + str(error))
    # The current display always clears; persisted completion still prevents retry.
    return {key: value for key, value in completed.items() if key not in MATERIAL_FIELDS}
