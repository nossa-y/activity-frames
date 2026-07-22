"""T06 - reproducibility / IVM certification.

VALIDATES: byte-identical compiled content run to run; rebuild == incremental;
append-only (earlier days never rewritten); compile cost does not grow with
history (O(delta)). A property no LLM-in-the-loop memory can pass.
"""
from _lib import Test, run_script


def main():
    t = Test("t06_ivm_certify", "byte-identical, rebuild==incremental, O(delta) compilation")
    d = run_script("certify_ivm.py")
    t.check("reproducibility byte-identical (content)", d["reproducibility_byte_identical"], expect=True)
    t.check("rebuild == incremental", d["rebuild_equals_incremental"], expect=True)
    t.check("append-only (earlier days unchanged)", d["append_only_earlier_days_unchanged"], expect=True)
    t.check("compile cost does not grow with history (<1.3x)",
            d["compile_ms"]["grows_with_history"], hi=1.3)
    t.check("verdict", d["verdict"], expect="PASS")
    return t.finish({"certification": d})


if __name__ == "__main__":
    main()
