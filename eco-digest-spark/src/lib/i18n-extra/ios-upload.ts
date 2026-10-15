// レポート取込（アップロード・貼り付け）の条件表示と事前チェックの文言。
export const ja: Record<string, string> = {
  "geo.ios.upload.conditionsTitle": "アップロードできるファイルの条件",
  "geo.ios.upload.conditionFormats": "形式: PDF・Word（.docx）・HTML・テキスト（.txt）",
  "geo.ios.upload.conditionSize": "サイズ: {mb}MBまで",
  "geo.ios.upload.conditionLength": "本文の長さ: 約{man}万文字まで（A4で約100ページ相当。それを超える場合は章ごとに分けてください）",
  "geo.ios.upload.conditionScan": "画像だけのPDF（スキャン）は読み取れません。文字を選択できるPDFを使ってください",
  "geo.ios.upload.conditionDoc": "古いWord形式（.doc）は非対応です。.docx に変換してください",
  "geo.ios.upload.errorExtension": "「{ext}」形式のファイルは取り込めません。PDF・Word（.docx）・HTML・テキスト（.txt）のいずれかを選んでください。",
  "geo.ios.upload.errorExtensionDoc": "古いWord形式（.doc）は取り込めません。Wordで開いて .docx で保存し直してください。",
  "geo.ios.upload.errorEmpty": "ファイルが空です。内容のあるファイルを選んでください。",
  "geo.ios.upload.errorTooLarge": "ファイルサイズが大きすぎます（{mb}MB）。{max}MBまでです。小さくするか、分割してください。",
  "geo.ios.upload.errorPasteTooLong": "貼り付けた文章が長すぎます（約{n}万文字）。約{man}万文字までです。章ごとに分けてください。",
  "geo.ios.upload.pasteCount": "{n}文字 / 上限 {max}文字",
};

export const en: Record<string, string> = {
  "geo.ios.upload.conditionsTitle": "Upload requirements",
  "geo.ios.upload.conditionFormats": "Formats: PDF, Word (.docx), HTML, text (.txt)",
  "geo.ios.upload.conditionSize": "Size: up to {mb} MB",
  "geo.ios.upload.conditionLength": "Length: up to about {man}0,000 characters (roughly 100 A4 pages). Split longer reports by chapter",
  "geo.ios.upload.conditionScan": "Image-only (scanned) PDFs cannot be read. Use a PDF with selectable text",
  "geo.ios.upload.conditionDoc": "Legacy Word (.doc) is not supported. Convert it to .docx",
  "geo.ios.upload.errorExtension": "Files of type \"{ext}\" cannot be imported. Choose PDF, Word (.docx), HTML or text (.txt).",
  "geo.ios.upload.errorExtensionDoc": "Legacy Word (.doc) cannot be imported. Open it in Word and save as .docx.",
  "geo.ios.upload.errorEmpty": "The file is empty. Choose a file with content.",
  "geo.ios.upload.errorTooLarge": "The file is too large ({mb} MB). The limit is {max} MB. Reduce or split it.",
  "geo.ios.upload.errorPasteTooLong": "The pasted text is too long (about {n}0,000 characters). The limit is about {man}0,000. Split it by chapter.",
  "geo.ios.upload.pasteCount": "{n} characters / limit {max}",
};
