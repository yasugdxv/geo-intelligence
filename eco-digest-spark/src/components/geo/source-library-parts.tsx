import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useLanguage } from "@/lib/i18n";
import {
  MEDIA_TYPES,
  useArchiveSource,
  useCreateSource,
  useUpdateSource,
  type CreateSourceResponse,
  type GeoSource,
  type GeoSourceTag,
  type SourceInput,
} from "@/lib/geoSourceLibraryApi";
import { cn } from "@/lib/utils";

// Source Library 共通の小部品（Tag表示 / 登録・編集Dialog / アーカイブ確認）。

const NONE = "__none";

/** Tag表示。AI提案で未確定のものは点線＋薄色にして、人が確定したものと区別する */
export function SourceTagChip({
  tag,
  className,
}: {
  tag: Pick<GeoSourceTag, "origin" | "is_confirmed" | "tag_value">;
  className?: string;
}) {
  const { t } = useLanguage();
  const pending = tag.origin === "ai" && !tag.is_confirmed;
  return (
    <Badge
      variant="outline"
      title={pending ? t("geo.sourceLibrary.tag.aiPending") : undefined}
      className={cn(
        "text-[10px] font-normal",
        pending && "border-dashed text-muted-foreground opacity-70",
        className,
      )}
    >
      {tag.tag_value}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// 登録・編集フォーム
// ---------------------------------------------------------------------------

interface FormState {
  name: string;
  url: string;
  media_type: string;
  region: string;
  languages: string;
  is_primary: boolean;
  description: string;
  update_cadence: string;
  reliability: string;
  notes: string;
  thematic_scope: string;
  suggest_tags: boolean;
}

function toForm(s?: GeoSource): FormState {
  return {
    name: s?.name ?? "",
    url: s?.url ?? "",
    media_type: s?.media_type ?? "",
    region: s?.region ?? "",
    languages: (s?.languages ?? []).join(", "),
    is_primary: s?.is_primary ?? false,
    description: s?.description ?? "",
    update_cadence: s?.update_cadence ?? "",
    reliability: s?.reliability ?? "",
    notes: s?.notes ?? "",
    thematic_scope: s?.thematic_scope ?? "",
    suggest_tags: false,
  };
}

function toInput(f: FormState, isEdit: boolean): SourceInput {
  // 編集時は空欄=クリア(null)、新規時は空欄=未送信
  const str = (v: string) => (v.trim() ? v.trim() : isEdit ? null : undefined);
  const langs = f.languages
    .split(/[,、\s]+/)
    .map((x) => x.trim())
    .filter(Boolean);
  return {
    name: f.name.trim(),
    url: str(f.url),
    media_type: str(f.media_type),
    region: str(f.region),
    languages: langs.length ? langs : isEdit ? [] : undefined,
    is_primary: f.is_primary,
    description: str(f.description),
    update_cadence: str(f.update_cadence),
    reliability: (f.reliability || (isEdit ? null : undefined)) as SourceInput["reliability"],
    notes: str(f.notes),
    thematic_scope: str(f.thematic_scope),
    ...(isEdit ? {} : { suggest_tags: f.suggest_tags }),
  };
}

function SourceForm({
  value,
  onChange,
  showSuggest,
}: {
  value: FormState;
  onChange: (v: FormState) => void;
  showSuggest: boolean;
}) {
  const { t } = useLanguage();
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => onChange({ ...value, [k]: v });
  const field = (label: string, node: React.ReactNode) => (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {node}
    </div>
  );
  return (
    <div className="grid gap-3 max-h-[60vh] overflow-y-auto pr-1">
      {field(
        `${t("geo.sourceLibrary.form.name")} *`,
        <Input value={value.name} onChange={(e) => set("name", e.target.value)} />,
      )}
      {field(
        t("geo.sourceLibrary.form.url"),
        <Input
          value={value.url}
          placeholder="https://"
          onChange={(e) => set("url", e.target.value)}
        />,
      )}
      <div className="grid grid-cols-2 gap-3">
        {field(
          t("geo.sourceLibrary.col.mediaType"),
          <Select
            value={value.media_type || NONE}
            onValueChange={(v) => set("media_type", v === NONE ? "" : v)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>{t("geo.sourceLibrary.any")}</SelectItem>
              {MEDIA_TYPES.map((m) => (
                <SelectItem key={m} value={m}>
                  {t(`geo.sourceLibrary.mediaType.${m}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>,
        )}
        {field(
          t("geo.sourceLibrary.form.reliability"),
          <Select
            value={value.reliability || NONE}
            onValueChange={(v) => set("reliability", v === NONE ? "" : v)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>{t("geo.sourceLibrary.any")}</SelectItem>
              {["high", "medium", "low"].map((r) => (
                <SelectItem key={r} value={r}>
                  {t(`geo.sourceLibrary.reliability.${r}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>,
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        {field(
          t("geo.sourceLibrary.col.region"),
          <Input value={value.region} onChange={(e) => set("region", e.target.value)} />,
        )}
        {field(
          t("geo.sourceLibrary.col.language"),
          <Input
            value={value.languages}
            placeholder="ja, en"
            onChange={(e) => set("languages", e.target.value)}
          />,
        )}
      </div>
      <label className="flex items-center gap-2 text-sm">
        <Checkbox
          checked={value.is_primary}
          onCheckedChange={(c) => set("is_primary", c === true)}
        />
        {t("geo.sourceLibrary.form.isPrimary")}
      </label>
      {field(
        t("geo.sourceLibrary.form.thematicScope"),
        <Input
          value={value.thematic_scope}
          onChange={(e) => set("thematic_scope", e.target.value)}
        />,
      )}
      {field(
        t("geo.sourceLibrary.form.updateCadence"),
        <Input
          value={value.update_cadence}
          onChange={(e) => set("update_cadence", e.target.value)}
        />,
      )}
      {field(
        t("geo.sourceLibrary.form.description"),
        <Textarea
          rows={2}
          value={value.description}
          onChange={(e) => set("description", e.target.value)}
        />,
      )}
      {field(
        t("geo.sourceLibrary.form.notes"),
        <Textarea rows={2} value={value.notes} onChange={(e) => set("notes", e.target.value)} />,
      )}
      {showSuggest && (
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={value.suggest_tags}
            onCheckedChange={(c) => set("suggest_tags", c === true)}
          />
          {t("geo.sourceLibrary.form.suggestTags")}
        </label>
      )}
    </div>
  );
}

/** 新規登録Dialog。登録後は警告（domain重複 / Tag提案失敗）を表示する結果画面に切り替える */
export function CreateSourceDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { t } = useLanguage();
  const create = useCreateSource();
  const [form, setForm] = useState<FormState>(toForm());
  const [result, setResult] = useState<CreateSourceResponse | null>(null);

  useEffect(() => {
    if (open) {
      setForm(toForm());
      setResult(null);
      create.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const submit = () => {
    create.mutate(toInput(form, false), { onSuccess: (r) => setResult(r) });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("geo.sourceLibrary.create.title")}</DialogTitle>
          <DialogDescription>{t("geo.sourceLibrary.create.desc")}</DialogDescription>
        </DialogHeader>
        {result ? (
          <div className="space-y-3 text-sm">
            <p className="font-medium">
              {t("geo.sourceLibrary.create.done", { name: result.source.name })}
            </p>
            {result.domain_conflicts.length > 0 && (
              <div className="rounded-md border border-geo-amber/50 bg-geo-amber-soft p-3 text-xs text-geo-amber flex gap-2">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                <div>
                  {t("geo.sourceLibrary.create.domainConflict")}
                  <div className="mt-1 opacity-80">{result.domain_conflicts.join(", ")}</div>
                </div>
              </div>
            )}
            {result.tag_suggestion?.error && (
              <div className="rounded-md border border-border bg-muted p-3 text-xs text-muted-foreground">
                {t("geo.sourceLibrary.create.tagFailed", { message: result.tag_suggestion.error })}
              </div>
            )}
            {result.tag_suggestion && !result.tag_suggestion.error && (
              <div className="text-xs text-muted-foreground">
                {t("geo.sourceLibrary.create.tagSuggested", {
                  count: result.tag_suggestion.tags.length,
                })}
              </div>
            )}
            <DialogFooter>
              <Button onClick={() => onOpenChange(false)}>{t("geo.sourceLibrary.close")}</Button>
            </DialogFooter>
          </div>
        ) : (
          <>
            <SourceForm value={form} onChange={setForm} showSuggest />
            {create.isError && (
              <p className="text-xs text-destructive">
                {t("geo.sourceLibrary.saveFailed", { message: (create.error as Error).message })}
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                {t("geo.sourceLibrary.cancel")}
              </Button>
              <Button disabled={!form.name.trim() || create.isPending} onClick={submit}>
                {create.isPending
                  ? t("geo.sourceLibrary.saving")
                  : t("geo.sourceLibrary.create.submit")}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** 編集Dialog（sourceId固定のhookを使うため、対象Sourceごとにマウントする） */
export function EditSourceDialog({ source, onClose }: { source: GeoSource; onClose: () => void }) {
  const { t } = useLanguage();
  const update = useUpdateSource(source.source_id);
  const [form, setForm] = useState<FormState>(toForm(source));
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("geo.sourceLibrary.edit.title")}</DialogTitle>
        </DialogHeader>
        <SourceForm value={form} onChange={setForm} showSuggest={false} />
        {update.isError && (
          <p className="text-xs text-destructive">
            {t("geo.sourceLibrary.saveFailed", { message: (update.error as Error).message })}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t("geo.sourceLibrary.cancel")}
          </Button>
          <Button
            disabled={!form.name.trim() || update.isPending}
            onClick={() => update.mutate(toInput(form, true), { onSuccess: onClose })}
          >
            {update.isPending ? t("geo.sourceLibrary.saving") : t("geo.sourceLibrary.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** アーカイブ（soft delete）確認Dialog */
export function ArchiveSourceDialog({
  source,
  onClose,
  onArchived,
}: {
  source: GeoSource;
  onClose: () => void;
  onArchived?: () => void;
}) {
  const { t } = useLanguage();
  const archive = useArchiveSource();
  return (
    <AlertDialog open onOpenChange={(o) => !o && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {t("geo.sourceLibrary.archive.title", { name: source.name })}
          </AlertDialogTitle>
          <AlertDialogDescription>{t("geo.sourceLibrary.archive.desc")}</AlertDialogDescription>
        </AlertDialogHeader>
        {archive.isError && (
          <p className="text-xs text-destructive">{(archive.error as Error).message}</p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>{t("geo.sourceLibrary.cancel")}</AlertDialogCancel>
          <AlertDialogAction
            disabled={archive.isPending}
            onClick={(e) => {
              // 成功時のみ閉じたいので既定の自動クローズを止める
              e.preventDefault();
              archive.mutate(source.source_id, {
                onSuccess: () => {
                  onArchived?.();
                  onClose();
                },
              });
            }}
          >
            {t("geo.sourceLibrary.archive.confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
