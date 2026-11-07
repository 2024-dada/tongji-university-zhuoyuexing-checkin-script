import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from checkin.deepseek import NoBalance
from checkin.images import ImageFinder, ImageSelectionRequired, results

SEARCH_QUERIES = ImageFinder.search_queries
from checkin.engine import Engine
from checkin.storage import History


class ImageSearchParsingTests(unittest.TestCase):
    @staticmethod
    def page(start, end):
        return '<input id="sb_form_q" value="校园讲座">' + ''.join('<a class="iusc" m="' + json.dumps(dict(murl=f'https://example.com/{i}.png',
            t=f'Image {i}', purl='https://example.com/')).replace('"', '&quot;') + '"></a>'
            for i in range(start, end))

    def test_second_result_page_deduplicates_and_caps_at_50_candidates(self):
        with patch('checkin.edge_search.render_page', side_effect=[self.page(0, 30), self.page(20, 70)]) as render:
            found = list(results('校园讲座'))
        self.assertEqual(render.call_count, 2)
        self.assertEqual(len(found), 50)
        self.assertEqual([row['url'] for row in found], [f'https://example.com/{i}.png' for i in range(50)])
        self.assertIn('/images/search?', render.call_args.args[0])
        self.assertIn('&first=', render.call_args.args[0])

    def test_first_candidate_is_available_before_fetching_second_page(self):
        with patch('checkin.edge_search.render_page', side_effect=[self.page(0, 2), self.page(2, 50)]) as render:
            candidates = results('校园讲座')
            self.assertEqual(next(candidates)['url'], 'https://example.com/0.png')
            render.assert_called_once()
            self.assertEqual(len(list(candidates)), 49)
            self.assertEqual(render.call_count, 2)

    def test_paging_failure_preserves_first_page_candidates(self):
        with patch('checkin.edge_search.render_page', side_effect=[self.page(0, 3), OSError('offline')]):
            found = list(results('校园讲座'))
        self.assertEqual(len(found), 3)

    def test_attribute_quote_styles_and_apostrophes_preserve_results_in_order(self):
        first = dict(murl='https://example.com/lecture.jpg', t="Professor's lecture", purl='https://example.com/')
        second = dict(murl='https://example.com/audience.jpg', t='University audience', purl='https://example.com/')
        double_attribute = json.dumps(first).replace('"', '&quot;')
        single_attribute = json.dumps(second)
        source = ('<input id="sb_form_q" value="校园讲座">'
                  '<a class="iusc" m="' + double_attribute + '"></a>'
                  '<a class="iusc" m="broken"></a><a class="iusc" m="[]"></a>'
                  '<a class="iusc" m="' + double_attribute + '"></a>'
                  "<a class='iusc' m='" + single_attribute + "'></a>")
        with patch('checkin.edge_search.render_page', return_value=source):
            found = list(results('校园讲座'))
        self.assertEqual([row['url'] for row in found], [first['murl'], second['murl']])
        self.assertEqual(found[0]['title'], first['t'])

    def test_wrong_search_query_is_rejected_instead_of_reviewing_unrelated_images(self):
        source = self.page(0, 3).replace('校园讲座', '不合格')
        with patch('checkin.edge_search.render_page', return_value=source):
            with self.assertRaisesRegex(ValueError, '关键词'):
                list(results('科坛趣话 书籍封面'))

    def test_non_image_card_metadata_and_invalid_urls_are_ignored(self):
        source = self.page(0, 1)
        wrong = json.dumps({'murl': 'https://example.com/advert.jpg'}).replace('"', '&quot;')
        bad_url = json.dumps({'murl': 'https://[broken'}).replace('"', '&quot;')
        source += '<div m="' + wrong + '"></div><a class="recommendation" m="' + wrong + '"></a>'
        source += '<a class="iusc" m="' + bad_url + '"></a>'
        with patch('checkin.edge_search.render_page', return_value=source):
            found = list(results('校园讲座'))
        self.assertEqual([row['url'] for row in found], ['https://example.com/0.png'])

    def test_search_stop_does_not_return_a_partial_list(self):
        with patch('checkin.edge_search.render_page', side_effect=[self.page(0, 3), RuntimeError('操作已停止')]):
            with self.assertRaisesRegex(RuntimeError, '停止'):
                list(results('校园讲座'))


class ImageFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cancel = threading.Event()
        self.api = Mock()
        self.api.json.return_value = {"match": False, "relevance": "related", "watermark_boxes": []}
        self.query_patch = patch.object(ImageFinder, "search_queries", return_value=["讲座 配图", "讲座 设备", "讲座", "讲座 现场", "讲座 主题"])
        self.query_patch.start()
        self.addCleanup(self.query_patch.stop)
        self.finder = ImageFinder(self.api, self.root, self.cancel, lambda text: None)
        self.candidates = [dict(url=f"https://example.com/{i}.png", title="讲座", page="https://example.com/") for i in range(60)]

    def data(self, url, **kwargs):
        number = int(url.rsplit("/", 1)[1].split(".")[0])
        buffer = io.BytesIO()
        Image.new("RGB", (200, 240), (number, 20, 30)).save(buffer, "PNG")
        return buffer.getvalue()

    def proof(self):
        return json.loads((self.root / "照片" / "row" / "配图选择.json").read_text("utf-8"))

    def test_50_related_rejections_use_best_fallback_and_reuse_with_hash(self):
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data) as download:
            path = self.finder.choose("求索之星", "讲座", "row")
        self.assertEqual(download.call_count, 50)
        self.assertEqual(self.api.json.call_count, 100)
        self.assertEqual(Image.open(path).getpixel((0, 0)), (0, 20, 30))
        self.assertEqual(self.proof()["reviewed"], 50)
        self.assertEqual(self.proof()["selection"], "best_after_limit")
        self.assertFalse((self.root / "照片" / "row" / "审查通过.json").exists())
        self.assertTrue(self.finder.ready("求索之星", "讲座", "row", path))
        self.assertIn("DeepSeek", self.finder.warning("row", path))
        Path(path).write_bytes(b"changed")
        self.assertFalse(self.finder.ready("求索之星", "讲座", "row", path))

    def test_50th_passes_so_does_not_fallback(self):
        good = {"match": True, "relevance": "related", "watermark_boxes": []}
        self.api.json.side_effect = [{"match": False, "relevance": "related", "watermark_boxes": []}] * 98 + [good, good]
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data) as download:
            path = self.finder.choose("求索之星", "讲座", "row")
        self.assertEqual(download.call_count, 50)
        self.assertEqual(Image.open(path).getpixel((0, 0)), (49, 20, 30))
        self.assertEqual(self.proof()["selection"], "approved")
        self.assertEqual(self.finder.warning("row", path), "")

    def test_invalid_first_download_uses_first_valid_image(self):
        def data(url, **kwargs):
            return b"not an image" if url.endswith("/0.png") else self.data(url)
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=data):
            path = self.finder.choose("求索之星", "讲座", "row")
        self.assertEqual(Image.open(path).getpixel((0, 0)), (1, 20, 30))
        self.assertEqual(self.proof()["reviewed"], 50)

    def test_exhausted_short_list_requires_manual_image_without_duplicate_review(self):
        with patch("checkin.images.results", return_value=self.candidates[:3]), patch("checkin.images.download", side_effect=self.data):
            with self.assertRaisesRegex(ImageSelectionRequired, "不足50张"):
                self.finder.choose("求索之星", "讲座", "row")
        self.assertEqual(self.api.json.call_count, 6)
        self.assertEqual(self.proof()["reviewed"], 3)
        self.assertEqual(self.proof()["selection"], "manual_required")
        self.assertFalse((self.root / "照片" / "row" / "上传配图.png").exists())

    def test_no_valid_image_stops(self):
        with patch("checkin.images.results", return_value=self.candidates[:3]), patch("checkin.images.download", return_value=b"invalid"):
            with self.assertRaisesRegex(ValueError, "正常打开"):
                self.finder.choose("求索之星", "讲座", "row")
        self.assertFalse((self.root / "照片" / "row" / "上传配图.png").exists())

    def test_no_balance_never_becomes_fallback(self):
        self.api.json.side_effect = NoBalance("请充值")
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data):
            with self.assertRaises(NoBalance):
                self.finder.choose("求索之星", "讲座", "row")
        self.assertFalse((self.root / "照片" / "row" / "配图选择.json").exists())

    def test_stop_during_last_review_never_becomes_fallback(self):
        def reject(*args):
            if self.api.json.call_count == 50:
                self.cancel.set()
            return {"match": False}
        self.api.json.side_effect = reject
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data):
            with self.assertRaisesRegex(RuntimeError, "停止"):
                self.finder.choose("求索之星", "讲座", "row")
        self.assertFalse((self.root / "照片" / "row" / "配图选择.json").exists())

    def test_prepare_persists_fallback_warning_and_keeps_pending(self):
        history = History(self.root)
        row = history.reserve("弘文之星", "图书", {"minimum_chars": 80})
        history.update(row["id"], text="已经生成的真实学习感想" * 10)
        self.api.balance.return_value = "可用余额"
        self.api.reflection.return_value = "已经生成的真实学习感想" * 10
        engine = Engine(self.root, {}, history, self.api, self.cancel, lambda text: None, Mock())
        engine.navigation = Mock(return_value=[])
        engine.desktop = Mock()
        engine.nodes = Mock(return_value=[])
        engine.clear_selector_query = Mock()
        engine.choose = Mock(return_value=("另一本书", {"minimum_chars": 80}))
        engine.open_selector = Mock()
        engine.select_existing = Mock()
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data):
            prepared = engine.prepare("弘文之星")
        restored = History(self.root).pending("弘文之星")
        self.assertNotEqual(prepared["id"], row["id"])
        self.assertEqual(restored["status"], "待提交")
        self.assertTrue(Path(restored["image"]).is_file())
        self.assertIn("DeepSeek", restored["image_warning"])
        self.assertEqual(restored["text"], "已经生成的真实学习感想" * 10)
        with self.assertRaises(ValueError):
            history.update(row["id"], object="不能替换原对象")

    def test_mingde_resumes_and_fills_without_any_image_step(self):
        self.api.balance.return_value = "可用余额"
        self.api.reflection.return_value = "感想" * 60
        history = History(self.root)
        row = history.reserve("明德之星", "老师", {"location": "老师的办公室", "college": "学院", "minimum_chars": 80})
        history.update(row["id"], text="感想" * 60, image="old-image.png", image_warning="旧图片警告")
        engine = Engine(self.root, {}, history, self.api, self.cancel, Mock(), Mock())
        engine.navigation = Mock(return_value=[])
        engine.desktop = Mock()
        engine.nodes = Mock(return_value=[])
        engine.clear_selector_query = Mock()
        engine.choose = Mock(return_value=("另一位老师", {"location": "另一位老师的办公室", "college": "学院", "minimum_chars": 80}))
        engine.open_selector = Mock()
        engine.select_existing = Mock()
        with patch("checkin.engine.ImageFinder", side_effect=AssertionError("明德不应准备图片")):
            prepared = engine.prepare("明德之星")
        engine.verify_object = Mock()
        engine.is_checkin_form = Mock(return_value=True)
        engine.fill_field = Mock(return_value=True)
        engine.fill_date = Mock()
        engine.submission_gaps = Mock(return_value=[])
        engine.upload = Mock(side_effect=AssertionError("明德不应上传图片"))
        engine.fill(prepared)
        engine.upload.assert_not_called()
        engine.fill_date.assert_called_once()
        self.assertEqual(history.pending("明德之星")["phase"], "已填写，等待系统提交结果")
        self.assertNotIn("旧图片警告", engine.report.call_args.args[0])

    def test_50_unrelated_reviews_choose_the_highest_deepseek_score(self):
        self.api.json.side_effect = [
            {"match": False, "relevance": "unrelated", "relevance_score": 12, "watermark_boxes": []}
            for _ in range(12)
        ] + [
            {"match": False, "relevance": "unrelated", "relevance_score": 88, "watermark_boxes": []}
        ] + [
            {"match": False, "relevance": "unrelated", "relevance_score": 18, "watermark_boxes": []}
            for _ in range(37)
        ]
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data):
            path = self.finder.choose("力行之星", "方程式赛车驾驶仿真体验", "row")
        self.assertEqual(self.api.json.call_count, 50)
        self.assertEqual(Image.open(path).getpixel((0, 0)), (12, 20, 30))
        self.assertEqual(self.proof()["selection"], "best_after_limit")
        self.assertEqual(self.proof()["review_index"], 13)
        self.assertEqual(self.proof()["relevance_score"], 88)
        self.assertTrue(self.finder.ready("力行之星", "方程式赛车驾驶仿真体验", "row", path))

    def test_all_50_scores_zero_still_selects_first_highest_tie(self):
        self.api.json.return_value = {"match": False, "relevance": "unrelated", "relevance_score": 0, "watermark_boxes": []}
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data) as download:
            path = self.finder.choose("力行之星", "活动", "row")
        self.assertEqual(download.call_count, 50)
        self.assertEqual(self.proof()["reviewed"], 50)
        self.assertEqual(self.proof()["relevance_score"], 0)
        self.assertEqual(self.proof()["review_index"], 1)
        self.assertTrue(self.finder.ready("力行之星", "活动", "row", path))
        self.assertIn("50张", self.finder.warning("row", path))

    def test_49_rejected_candidates_do_not_trigger_50_candidate_fallback(self):
        with patch("checkin.images.results", return_value=self.candidates[:49]), patch("checkin.images.download", side_effect=self.data):
            with self.assertRaisesRegex(ImageSelectionRequired, "不足50张"):
                self.finder.choose("力行之星", "活动", "row")
        self.assertEqual(self.proof()["reviewed"], 49)
        self.assertEqual(self.proof()["selection"], "manual_required")

    def test_best_score_beats_first_candidate_when_first_is_unrelated(self):
        self.api.json.side_effect = [{"match": False, "relevance": "unrelated"}] + [
            {"match": False, "relevance": "related", "watermark_boxes": []}] * 98
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data):
            path = self.finder.choose("求索之星", "讲座", "row")
        self.assertEqual(Image.open(path).getpixel((0, 0)), (1, 20, 30))
        self.assertEqual(self.proof()["selection"], "best_after_limit")
        self.assertEqual(self.proof()["reviewed"], 50)

    def test_final_rejection_lowers_candidate_rank(self):
        related = {"match": False, "relevance": "related", "watermark_boxes": []}
        self.api.json.side_effect = [related, {"match": False, "relevance": "unrelated"}] + [related] * 98
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data):
            path = self.finder.choose("求索之星", "讲座", "row")
        self.assertEqual(Image.open(path).getpixel((1, 0)), (1, 20, 30))
        self.assertEqual(self.proof()["selection"], "best_after_limit")
        self.assertEqual(self.proof()["review_index"], 2)

    def test_uncertain_or_missing_relevance_still_selects_relative_best(self):
        for relevance in ("uncertain", None):
            with self.subTest(relevance=relevance):
                self.api.json.return_value = {"match": False, "relevance": relevance}
                with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data):
                    path = self.finder.choose("求索之星", "讲座", "row")
                self.assertEqual(self.proof()["selection"], "best_after_limit")
                self.assertEqual(self.proof()["reviewed"], 50)
                self.assertTrue(self.finder.ready("求索之星", "讲座", "row", path))

    def test_old_fallback_proof_cannot_be_reused(self):
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data):
            path = self.finder.choose("求索之星", "讲座", "row")
        proof = self.proof()
        proof.update(version=2, selection="first_after_limit", reviewed=2)
        (self.root / "照片" / "row" / "配图选择.json").write_text(json.dumps(proof), "utf-8")
        self.assertFalse(self.finder.ready("求索之星", "讲座", "row", path))

    def test_related_fallback_cache_must_have_50_reviews_and_matching_identity(self):
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data):
            path = self.finder.choose("求索之星", "讲座", "row")
        saved = self.proof()
        for changes in ({"reviewed": 2}, {"reviewed": 25}, {"version": 5}, {"name": "其他讲座"}):
            proof = dict(saved, **changes)
            (self.root / "照片" / "row" / "配图选择.json").write_text(json.dumps(proof), "utf-8")
            self.assertFalse(self.finder.ready("求索之星", "讲座", "row", path))

    def test_short_initial_search_continues_other_queries_and_accepts_later_match(self):
        rejected = {"match": False, "relevance": "unrelated"}
        good = {"match": True, "relevance": "related", "watermark_boxes": []}
        self.api.json.side_effect = [rejected, rejected, good, good]
        with patch("checkin.images.results", side_effect=[self.candidates[:2], self.candidates[2:3]]) as results, patch("checkin.images.download", side_effect=self.data):
            path = self.finder.choose("求索之星", "讲座", "row")
        self.assertEqual(results.call_count, 2)
        self.assertEqual(Image.open(path).getpixel((0, 0)), (2, 20, 30))
        self.assertEqual(self.proof()["selection"], "approved")

    def test_semantic_queries_put_racing_equipment_before_full_activity_title(self):
        self.api.json.return_value = {"queries": ["方程式赛车 驾驶模拟器", "赛车模拟驾驶 设备"]}
        name = "“济创X空间”数智体验系列活动——方程式赛车驾驶仿真体验"
        queries = SEARCH_QUERIES(self.finder, "力行之星", name)
        self.assertEqual(queries[0], "方程式赛车 驾驶模拟器")
        self.assertIn(name + " 相关图片", queries)
        self.assertEqual(len(queries), len(set(queries)))

    def test_empty_keyword_answer_still_extracts_actual_activity_topic(self):
        self.api.json.return_value = {"queries": []}
        queries = SEARCH_QUERIES(self.finder, "力行之星", "系列活动——方程式赛车驾驶仿真体验")
        self.assertEqual(queries[0], "方程式赛车 驾驶模拟器")
        self.assertIn("方程式赛车驾驶仿真体验", queries)

    def test_book_queries_do_not_call_keyword_ai(self):
        self.assertEqual(SEARCH_QUERIES(self.finder, "弘文之星", "实践论")[0], "实践论 图书 正面封面")
        self.api.json.assert_not_called()
        unrelated = {"is_book_cover": False, "title_matches": False, "visible_title": ""}
        wrong_book = {"is_book_cover": True, "title_matches": True, "visible_title": "其他书"}
        self.assertFalse(self.finder.fallback_eligible("弘文之星", "实践论", unrelated))
        self.assertFalse(self.finder.fallback_eligible("弘文之星", "实践论", wrong_book))

    def test_long_book_title_uses_core_title_and_does_not_double_book_brackets(self):
        queries = SEARCH_QUERIES(self.finder, '弘文之星', '《科坛趣话——科学、科学家与科学家精神》')
        self.assertEqual(queries[0], '科坛趣话 图书 正面封面')
        self.assertTrue(all('《《' not in query and '》》' not in query for query in queries))
        self.api.json.assert_not_called()

    def test_named_lecture_series_has_a_scene_query_without_the_brand(self):
        self.api.json.return_value = {"queries": ["启明星讲坛 讲座", "启明星讲坛 活动现场", "启明星 讲坛 活动"]}
        queries = SEARCH_QUERIES(self.finder, "力行之星", "启明星讲坛")
        self.assertIn("同济大学 校园讲座 现场", queries)
        self.assertIn("大学 讲座 听众", queries)
        self.assertLessEqual(len(queries), 8)

    def test_exhausted_search_preserves_text_but_skips_object_and_reports_it_to_gui(self):
        from checkin.engine import MaterialUnavailable
        history = History(self.root)
        row = history.reserve("弘文之星", "图书", {"minimum_chars": 80})
        history.update(row["id"], text="真实学习感想" * 20)
        self.api.balance.return_value = "可用余额"
        self.api.reflection.return_value = "真实学习感想" * 20
        on_row = Mock()
        engine = Engine(self.root, {}, history, self.api, self.cancel, Mock(), Mock(), on_row)
        engine.navigation = Mock(return_value=[])
        engine.desktop = Mock()
        engine.nodes = Mock(return_value=[])
        engine.clear_selector_query = Mock()
        engine.choose = Mock(return_value=("另一本书", {"minimum_chars": 80}))
        engine.open_selector = Mock()
        engine.select_existing = Mock()
        self.api.json.return_value = {"match": False, "relevance": "unrelated"}
        with patch("checkin.images.results", return_value=self.candidates[:2]), patch("checkin.images.download", side_effect=self.data):
            with self.assertRaises(MaterialUnavailable):
                engine.prepare("弘文之星")
        restarted = History(self.root)
        self.assertIsNone(restarted.pending("弘文之星"))
        restored = restarted.rows[-1]
        self.assertEqual(restored["text"], "真实学习感想" * 20)
        self.assertEqual(restored["status"], "已跳过")
        self.assertEqual(restored["image"], "")
        self.assertTrue(restored["phase"].startswith("资料不可用，已跳过："))
        self.assertIn("仅审查2/50张", restored["image_warning"])
        self.assertEqual(on_row.call_args.args[0]["status"], "已跳过")


    def test_noisy_first_query_does_not_spend_whole_budget(self):
        rejected = {"match": False, "relevance": "unrelated"}
        good = {"match": True, "relevance": "related", "watermark_boxes": []}
        self.api.json.side_effect = [rejected] * 5 + [good, good]
        with patch("checkin.images.results", side_effect=[self.candidates[:50], self.candidates[50:]]) as results, patch("checkin.images.download", side_effect=self.data):
            path = self.finder.choose("求索之星", "讲座", "row")
        self.assertEqual(results.call_count, 2)
        self.assertEqual(Image.open(path).getpixel((0, 0)), (50, 20, 30))

    def test_deferred_first_query_results_fill_budget_and_choose_best_fallback(self):
        self.api.json.return_value = {"match": False, "relevance": "unrelated"}
        with patch("checkin.images.results", side_effect=[self.candidates[:50], [], [], [], []]), patch("checkin.images.download", side_effect=self.data):
            path = self.finder.choose("求索之星", "讲座", "row")
        self.assertEqual(self.api.json.call_count, 50)
        self.assertEqual(self.proof()["reviewed"], 50)
        self.assertEqual(self.proof()["selection"], "best_after_limit")
        self.assertEqual(Image.open(path).getpixel((0, 0)), (0, 20, 30))

    def test_switch_all_candidates_in_order_without_network_or_review(self):
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data):
            self.finder.choose("求索之星", "讲座", "row")
        self.api.json.reset_mock()
        with patch("checkin.images.results", side_effect=AssertionError("不能重新搜索")), patch("checkin.images.download", side_effect=AssertionError("不能重新下载")):
            for index in range(2, 51):
                path = self.finder.select_reviewed("求索之星", "讲座", "row")
                self.assertEqual(Image.open(path).getpixel((0, 0)), (index - 1, 20, 30))
                self.assertEqual(self.proof()["review_index"], index)
                self.assertTrue(self.finder.ready("求索之星", "讲座", "row", path))
            with self.assertRaisesRegex(ValueError, "最后一张"):
                self.finder.select_reviewed("求索之星", "讲座", "row")
        self.assertEqual(self.proof()["review_index"], 50)
        self.api.json.assert_not_called()

    def test_tampered_candidate_does_not_replace_selected_image(self):
        with patch("checkin.images.results", return_value=self.candidates), patch("checkin.images.download", side_effect=self.data):
            path = self.finder.choose("求索之星", "讲座", "row")
        original = Path(path).read_bytes()
        (Path(path).parent / "候选-02.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.finder.select_reviewed("求索之星", "讲座", "row")
        self.assertEqual(Path(path).read_bytes(), original)
        self.assertEqual(self.proof()["review_index"], 1)

    def test_shizhi_cannot_use_manual_candidate_policy(self):
        with self.assertRaises(ValueError):
            self.finder.select_reviewed("矢志之星", "地标", "row")

    def test_legacy_record_without_candidates_explains_research_needed(self):
        with self.assertRaisesRegex(ValueError, "没有保存审查候选"):
            self.finder.select_reviewed("求索之星", "讲座", "row")

