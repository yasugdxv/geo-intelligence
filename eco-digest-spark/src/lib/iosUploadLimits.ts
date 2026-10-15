// レポート取込（アップロード・貼り付け）の条件。
// バックエンドの geo_ios_report_draft_service.IMPORT_* と同じ値にする（APIも同じ条件で確認する）。

export const IMPORT_ACCEPT = ".pdf,.docx,.txt,.html,.htm";
export const IMPORT_EXTENSIONS = [".pdf", ".docx", ".txt", ".html", ".htm"];
export const IMPORT_MAX_FILE_MB = 20;
export const IMPORT_MAX_TEXT_CHARS = 150_000;

export type ImportProblem =
  | { kind: "extension"; ext: string }
  | { kind: "empty" }
  | { kind: "tooLarge"; mb: string };

/** 選んだファイルを送る前に確認する。問題が無ければ null */
export function checkImportFile(file: File): ImportProblem | null {
  const name = file.name.toLowerCase();
  if (!IMPORT_EXTENSIONS.some((e) => name.endsWith(e))) {
    const dot = name.lastIndexOf(".");
    return { kind: "extension", ext: dot >= 0 ? name.slice(dot) : "" };
  }
  if (file.size <= 0) return { kind: "empty" };
  if (file.size > IMPORT_MAX_FILE_MB * 1024 * 1024) {
    return { kind: "tooLarge", mb: (file.size / (1024 * 1024)).toFixed(1) };
  }
  return null;
}
