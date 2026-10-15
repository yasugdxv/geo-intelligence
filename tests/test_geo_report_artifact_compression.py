import geo_report_artifact_compression as comp


def test_compress_then_decompress_roundtrips():
    original = "a,b,c\n" * 1000
    compressed = comp.compress_text(original)
    assert compressed != original
    assert comp.decompress_text(compressed) == original


def test_compress_produces_smaller_output_for_repetitive_text():
    original = "x" * 1_000_000
    compressed = comp.compress_text(original)
    assert len(compressed) < len(original)


def test_decompress_passes_through_plain_uncompressed_text():
    plain = "hello,world\n1,2\n"
    assert comp.decompress_text(plain) == plain


def test_decompress_passes_through_none():
    assert comp.decompress_text(None) is None


def test_decompress_handles_unicode_content():
    original = "日本語のCSV,テスト\n公共の声,データ\n"
    compressed = comp.compress_text(original)
    assert comp.decompress_text(compressed) == original
