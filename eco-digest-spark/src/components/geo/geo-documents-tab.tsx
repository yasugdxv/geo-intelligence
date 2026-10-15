import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { useGeoDocuments, useGeoUploadDocument, useGeoExtractEvents } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";

export function GeoDocumentsTab({ themeId }: { themeId: string }) {
  const { t } = useLanguage();
  const { data, isLoading } = useGeoDocuments(themeId);
  const documents = data?.documents ?? [];

  const uploadMutation = useGeoUploadDocument();
  const extractMutation = useGeoExtractEvents();
  const [extractingId, setExtractingId] = useState<string | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [extractMessage, setExtractMessage] = useState<string | null>(null);

  const fileRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [isMock, setIsMock] = useState(false);

  const onUpload = () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setUploadMessage(null);
    uploadMutation.mutate(
      { file, themeId, title, url, isMock },
      {
        onSuccess: (result) => {
          setUploadMessage(result.isNew ? t("geo.documents.uploadSuccessNew") : t("geo.documents.uploadSuccessDup"));
          setTitle("");
          setUrl("");
          setIsMock(false);
          if (fileRef.current) fileRef.current.value = "";
        },
      },
    );
  };

  const onExtract = (documentId: string) => {
    setExtractingId(documentId);
    setExtractMessage(null);
    extractMutation.mutate(documentId, {
      onSuccess: (result) => setExtractMessage(t("geo.documents.extractedCount", { count: result.eventsCreated })),
      onSettled: () => setExtractingId(null),
    });
  };

  return (
    <div className="space-y-6">
      <p className="text-xs text-muted-foreground">{t("geo.tabs.documentsExplain")}</p>
      <div className="card-paper rounded-lg p-5 space-y-3">
        <h2 className="text-editorial text-lg">{t("geo.documents.uploadTitle")}</h2>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label>{t("geo.documents.fieldTitle")}</Label>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>{t("geo.documents.fieldUrl")}</Label>
            <Input value={url} onChange={(e) => setUrl(e.target.value)} />
          </div>
          <div className="space-y-1 col-span-2">
            <Label>{t("geo.documents.fieldFile")}</Label>
            <Input ref={fileRef} type="file" accept=".pdf,.html,.htm,.txt,.docx" />
          </div>
          <div className="flex items-center gap-2 col-span-2">
            <Checkbox id="geo-doc-mock" checked={isMock} onCheckedChange={(v) => setIsMock(v === true)} />
            <Label htmlFor="geo-doc-mock" className="font-normal">{t("geo.documents.fieldMock")}</Label>
          </div>
        </div>
        <Button onClick={onUpload} disabled={uploadMutation.isPending}>
          {uploadMutation.isPending ? t("geo.documents.uploading") : t("geo.documents.uploadButton")}
        </Button>
        {uploadMessage && <p className="text-xs text-muted-foreground">{uploadMessage}</p>}
      </div>

      {isLoading ? (
        <p className="text-muted-foreground">...</p>
      ) : documents.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("geo.documents.empty")}</p>
      ) : (
        <div className="space-y-3">
          {extractMessage && <p className="text-xs text-muted-foreground">{extractMessage}</p>}
          {documents.map((d) => (
            <div key={d.id} className="card-paper rounded-lg p-4 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{d.title}</span>
                <div className="flex items-center gap-2">
                  {d.isMock && <Badge variant="outline">{t("geo.overview.mockBadge")}</Badge>}
                  <Badge variant={d.fetchStatus === "成功" ? "secondary" : "destructive"}>{d.fetchStatus}</Badge>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                {t("geo.documents.fetchedAt")}: {d.fetchedAt}
              </p>
              <p className="text-sm text-muted-foreground line-clamp-2">{d.extractedTextPreview}</p>
              <Button
                size="sm" variant="outline"
                onClick={() => onExtract(d.id)}
                disabled={extractingId === d.id}
              >
                {extractingId === d.id ? t("geo.documents.extracting") : t("geo.documents.extractButton")}
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
