"""Publish one prepared form once and persist the system result."""
import time
from datetime import datetime
from .storage import CHINA, today
from .thumbnail import matches_uploaded_photo
from .submit_watch import SubmitWatch

def classify(message):
    text = ''.join(str(message or '').split())
    if any(w in text for w in ('登录已过期', '请重新登录')):
        return 'error'
    if any(w in text for w in ('已打卡', '已经打卡', '不能重复提交', '请勿重复提交', '请勿重复打卡')):
        return 'already'
    if any(w in text for w in ('提交失败', '发布失败', '打卡失败')):
        return 'error'
    if any(w in text for w in ('提交成功', '发布成功', '打卡成功')):
        return 'success'
    # Successful submission navigates to the module chooser. OCR can merge the
    # new toast with another theme's static "quota exhausted" card.
    if any(w in text for w in ('次数已用完', '今日次数不足')):
        return 'error'
    return None


def find_submit_button(engine, row):
    from .engine import NeedUser
    nodes = engine.field_controls(engine.nodes())
    if not engine.is_checkin_form(row['theme'], nodes):
        raise NeedUser('当前不是本主题已打开的表单，不能发布。')
    gaps = engine.submission_gaps(row, nodes)
    if gaps:
        raise NeedUser('表单仍有必填内容未核实：' + '、'.join(gaps) + '。不会点击“确认发布”。')

    # Some WeCom builds expose validation hints as text but omit the matching
    # input's current value. Treat a visible required-field warning as a hard
    # stop even if a control was otherwise misread.
    messages = [''.join(str(n.get(key) or '') for key in ('value', 'name')).replace(' ', '')
                for n in nodes if n.get('kind') in ('Text', 'StaticText', 'StatusBar', 'Alert') or n.get('id') == 'ocr']
    validation = [message for message in messages if any(marker in message for marker in (
        '请填写', '不能为空', '至少输入', '字数不足', '字数不够'))]
    if validation:
        raise NeedUser('页面仍显示必填校验提示：“' + validation[0][:120] + '”。不会点击“确认发布”。')
    if row['theme'] != '明德之星' and not matches_uploaded_photo(engine.desktop, nodes, row.get('image')):
        raise NeedUser('当前表单未显示本条配图，已暂停，避免把图片错误地提交到其他对象。')
    buttons = engine.unique_click_targets([n for n in nodes if str(n.get('name', '')).strip() == '确认发布' and n.get('enabled', True)])
    if len(buttons) != 1:
        raise NeedUser('当前页面无法唯一识别“确认发布”按钮，已暂停。')
    return min(buttons, key=lambda n: n['width']*n['height'])


def publish(engine, row):
    from .engine import NeedUser
    if row['status'] != '待提交':
        raise NeedUser('记录当前不允许自动发布，不能重提结果待核实的对象。')
    observer = None
    try:
        engine.desktop.overlay_visibility(True)
        button = find_submit_button(engine, row)
        observer = SubmitWatch(engine.desktop, wait_for_arm=True)
        # The watcher can take time to start; do not reuse a button from the
        # observation made before it was ready.
        button = find_submit_button(engine, row)
        # A full UIA/OCR read can be slow. Start the bounded toast window only
        # after this last read, immediately before the durable save and click.
        observer.arm()
        # Save before clicking. Crash/stop after this point cannot cause retry.
        engine.history.update(row['id'], status='结果待核实', phase='正在提交，等待系统结果',
                              submit_started_at=datetime.now(CHINA).isoformat())
        engine.on_row(row.copy())
        engine.report('自动点击“确认发布”一次，等待系统返回结果……')
        engine.desktop.click(button, allow_protected=True)
        deadline = time.monotonic()+45
        while time.monotonic() < deadline:
            engine.desktop.pause(.1)
            message = observer.result()
            result = classify(message)
            if not result:
                if observer.process.poll() is not None:
                    engine.history.update(row['id'], phase='提交结果待核实')
                    raise NeedUser('提交监听结束但没有明确系统结果，已暂停，不会自动重提。')
                continue
            if result == 'error':
                engine.history.update(row['id'], phase='提交结果待核实', submit_message=message[:800])
                raise NeedUser('系统报告失败、次数不足或登录变化：' + message[:250] + '；已暂停，不会自动重提。')
            completed = engine.history.update(row['id'], status='已完成', date=today(),
                confirmed_at=datetime.now(CHINA).isoformat(), submit_result=result, submit_message=message[:800],
                phase='系统提示此前已打卡，下次跳过' if result == 'already' else '系统确认自动提交成功')
            return result, completed
        engine.history.update(row['id'], phase='提交结果待核实')
        raise NeedUser('45 秒内未读到明确提交结果，已保存为结果待核实；不会自动再次发布。')
    finally:
        try:
            if observer:
                observer.close()
        finally:
            engine.desktop.overlay_visibility(False)
