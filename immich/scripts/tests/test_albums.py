"""文件夹名解析与相簿日期推导的测试（immich.albums）。"""

import unittest
from datetime import date

from immich.albums import (
    album_span,
    asset_date,
    cluster_bounds,
    derive_album_name,
    match_album,
    modal_date,
    parse_name,
)


class ParseNameTests(unittest.TestCase):
    """来自 /mnt/album/旅行 的真实文件夹名（2026-10 验证）。"""

    def test_year_month_only(self):
        start, end, loc = parse_name("2002.07连云港")
        self.assertIsNone(start)
        self.assertIsNone(end)
        self.assertEqual(loc, "连云港")

    def test_full_start_date(self):
        start, end, loc = parse_name("2005.01.11海南照片")
        self.assertEqual(start, date(2005, 1, 11))
        self.assertIsNone(end)
        self.assertEqual(loc, "海南照片")

    def test_compact_end_mmdd(self):
        start, end, loc = parse_name("2006.09.08-0914九寨沟黄龙")
        self.assertEqual((start, end), (date(2006, 9, 8), date(2006, 9, 14)))
        self.assertEqual(loc, "九寨沟黄龙")

    def test_end_day_only(self):
        start, end, _ = parse_name("2008.07.07-08香港旅游")
        self.assertEqual((start, end), (date(2008, 7, 7), date(2008, 7, 8)))

    def test_compact_start_and_day_end(self):
        start, end, _ = parse_name("20080708-09香港深圳")
        self.assertEqual((start, end), (date(2008, 7, 8), date(2008, 7, 9)))

    def test_end_month_day_dotted(self):
        start, end, _ = parse_name("2008.07.28-08.03哈尔滨之旅")
        self.assertEqual((start, end), (date(2008, 7, 28), date(2008, 8, 3)))

    def test_end_day_two_digits(self):
        start, end, _ = parse_name("2010.07.15-22无锡舟山苏州周庄上海")
        self.assertEqual((start, end), (date(2010, 7, 15), date(2010, 7, 22)))

    def test_tilde_separator(self):
        start, end, _ = parse_name("2013.07.11~07.15三亚")
        self.assertEqual((start, end), (date(2013, 7, 11), date(2013, 7, 15)))

    def test_compact_month_day_start(self):
        # "2014.0805" = 2014-08-05；结束 0810 = MMDD
        start, end, _ = parse_name("2014.0805~0810三亚旅行")
        self.assertEqual((start, end), (date(2014, 8, 5), date(2014, 8, 10)))

    def test_all_compact(self):
        start, end, _ = parse_name("20141003~1004黄陂游")
        self.assertEqual((start, end), (date(2014, 10, 3), date(2014, 10, 4)))

    def test_full_end_with_year(self):
        start, end, _ = parse_name("20241230-20250102跨年")
        self.assertEqual((start, end), (date(2024, 12, 30), date(2025, 1, 2)))

    def test_location_with_parens(self):
        _, _, loc = parse_name("2013.01.25-29广州(相机)")
        self.assertEqual(loc, "广州(相机)")

    def test_no_date_prefix(self):
        start, end, loc = parse_name("未整理")
        self.assertIsNone(start)
        self.assertEqual(loc, "未整理")


class SpanTests(unittest.TestCase):
    def test_same_year(self):
        self.assertEqual(album_span(date(2008, 12, 9), date(2008, 12, 14)), "20081209-1214")

    def test_cross_year(self):
        self.assertEqual(album_span(date(2024, 12, 30), date(2025, 1, 2)), "20241230-20250102")


def make_asset(dto=None, local=None, fca="2015-04-25T00:00:00Z", aid="a1"):
    asset = {"id": aid, "fileCreatedAt": fca}
    if local is not None:
        asset["localDateTime"] = local
    if dto is not None:
        asset["exifInfo"] = {"dateTimeOriginal": dto}
    return asset


class AssetDateTests(unittest.TestCase):
    def test_real_exif_wins(self):
        d, src = asset_date(make_asset(dto="2018-08-05T21:43:46.963+00:00", local="2018-08-06T06:43:46.963Z"))
        self.assertEqual((d, src), (date(2018, 8, 6), "exif"))  # 本地日历日

    def test_missing_exif_falls_back_to_file_created(self):
        d, src = asset_date(make_asset())
        self.assertEqual((d, src), (date(2015, 4, 25), "file"))


class ClusterBoundsTests(unittest.TestCase):
    def test_camera_clock_stray_trimmed(self):
        dates = [date(2018, 8, d) for d in range(1, 28)] + [date(2018, 10, 30)]
        lo, hi = cluster_bounds(sorted(dates))
        # 无众数（每天一张）-> P5/P95；边缘最多损失约 5%
        self.assertEqual((lo, hi), (date(2018, 8, 2), date(2018, 8, 27)))

    def test_poster_frame_generation_dates_trimmed(self):
        dates = sorted([date(2015, 4, 25)] * 48 + [date(2025, 1, 24)] * 3)
        lo, hi = cluster_bounds(dates)
        self.assertEqual((lo, hi), (date(2015, 4, 25), date(2015, 4, 25)))

    def test_no_modal_uses_percentiles(self):
        dates = sorted(date(2020, 1, d) for d in range(1, 21))
        lo, hi = cluster_bounds(dates)
        self.assertEqual((lo, hi), (date(2020, 1, 2), date(2020, 1, 20)))


class ModalDateTests(unittest.TestCase):
    def test_modal_frequency(self):
        m, freq = modal_date([date(2010, 5, 30)] * 4 + [date(2010, 7, 15)])
        self.assertEqual((m, freq), (date(2010, 5, 30), 0.8))


class DeriveAlbumNameTests(unittest.TestCase):
    def test_name_dates_are_authoritative(self):
        entry = derive_album_name("2009.08.24-08.29云南", [make_asset(aid=str(i)) for i in range(3)])
        self.assertEqual(entry["album_name"], "20090824-0829云南")
        self.assertEqual(entry["flags"], [])

    def test_exif_fills_missing_end(self):
        assets = [
            make_asset(dto=f"2006-03-2{d}T10:00:00Z", local=f"2006-03-2{d}T18:00:00Z", aid=str(d))
            for d in (3, 4, 5)
        ]
        entry = derive_album_name("2006.03苏州之行", assets)
        self.assertEqual(entry["album_name"], "20060323-0325苏州之行")

    def test_poster_frames_do_not_pollute_end(self):
        # 名称只给起始日；48 张真照片在 0425，3 张海报帧带 2025 年 EXIF
        assets = [make_asset(dto="2015-04-25T10:00:00Z", local="2015-04-25T18:00:00Z", aid=f"r{i}") for i in range(48)]
        assets += [make_asset(dto="2025-01-24T10:00:00Z", local="2025-01-24T18:00:00Z", aid=f"p{i}") for i in range(3)]
        entry = derive_album_name("20150425黄石看槐花", assets)
        self.assertEqual(entry["album_name"], "20150425-0425黄石看槐花")

    def test_all_file_dates_same_day_far_after_name_start(self):
        # 17 张照片 2007-03-31 拷贝、文件夹名是 03-11：拷贝日期不得延长行程
        assets = [make_asset(fca="2007-03-31T12:00:00Z", aid=str(i)) for i in range(17)]
        entry = derive_album_name("2007.03.11云台山春游", assets)
        self.assertEqual(entry["album_name"], "20070311-0311云台山春游")

    def test_exif_contradiction_flagged(self):
        assets = [make_asset(fca="2007-03-31T12:00:00Z", aid=str(i)) for i in range(17)]
        entry = derive_album_name("2007.03.11云台山春游", assets)
        # 完全没有 EXIF 时 end 等于 start；有 EXIF 才会标记
        self.assertEqual(entry["flags"], [])
        assets2 = [make_asset(dto="2007-03-31T10:00:00Z", local="2007-03-31T18:00:00Z", aid=str(i)) for i in range(17)]
        entry2 = derive_album_name("2007.03.11云台山春游", assets2)
        self.assertTrue(any("请复核" in f for f in entry2["flags"]))


class MatchAlbumTests(unittest.TestCase):
    def test_exact_normalized(self):
        al = match_album("20120629-0701庐山", [{"albumName": "2012.06.29-07.01庐山"}])
        self.assertEqual(al["albumName"], "2012.06.29-07.01庐山")

    def test_parsed_match_tolerates_separators(self):
        al = match_album("20080728-0803哈尔滨之旅", [{"albumName": "20080728~0803哈尔滨之旅"}])
        self.assertIsNotNone(al)

    def test_no_match(self):
        self.assertIsNone(match_album("20020721-0725连云港", [{"albumName": "Screenshots"}]))


if __name__ == "__main__":
    unittest.main()
