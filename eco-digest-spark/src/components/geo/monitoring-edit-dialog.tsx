import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
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
import { useUpdateMonitoringTheme, type MonitoringTheme } from "@/lib/geoSourceLibraryApi";

const splitCsv = (s: string) =>
  s
    .split(/[,、]/)
    .map((x) => x.trim())
    .filter(Boolean);

// Monitoring設定の編集ダイアログ（シンプルなフォームのみ）
export function MonitoringEditDialog({
  theme,
  open,
  onOpenChange,
}: {
  theme: MonitoringTheme;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useLanguage();
  const update = useUpdateMonitoringTheme(theme.monitoring_theme_id);
  const [name, setName] = useState("");
  const [keywords, setKeywords] = useState("");
  const [actors, setActors] = useState("");
  const [criteria, setCriteria] = useState("");
  const [sensitivity, setSensitivity] = useState<MonitoringTheme["sensitivity"]>("medium");
  const [frequency, setFrequency] = useState<MonitoringTheme["frequency"]>("daily");
  const [status, setStatus] = useState<"active" | "paused">("active");

  // 開くたびに現在の値で初期化する
  useEffect(() => {
    if (!open) return;
    setName(theme.display_name);
    setKeywords(theme.keywords.join(", "));
    setActors(theme.actors.join(", "));
    setCriteria(theme.monitoring_criteria ?? "");
    setSensitivity(theme.sensitivity);
    setFrequency(theme.frequency);
    setStatus(theme.status === "paused" ? "paused" : "active");
    update.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const save = () => {
    update.mutate(
      {
        display_name: name.trim() || theme.display_name,
        keywords: splitCsv(keywords),
        actors: splitCsv(actors),
        monitoring_criteria: criteria.trim() || null,
        sensitivity,
        frequency,
        status,
      },
      { onSuccess: () => onOpenChange(false) },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("geo.monitoringThemes.edit.title")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="space-y-1">
            <Label>{t("geo.monitoringThemes.edit.name")}</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>{t("geo.monitoringThemes.edit.keywords")}</Label>
            <Input value={keywords} onChange={(e) => setKeywords(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>{t("geo.monitoringThemes.edit.actors")}</Label>
            <Input value={actors} onChange={(e) => setActors(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>{t("geo.monitoringThemes.edit.criteria")}</Label>
            <Textarea value={criteria} onChange={(e) => setCriteria(e.target.value)} rows={3} />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div className="space-y-1">
              <Label>{t("geo.monitoringThemes.edit.sensitivity")}</Label>
              <Select
                value={sensitivity}
                onValueChange={(v) => setSensitivity(v as typeof sensitivity)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(["low", "medium", "high"] as const).map((v) => (
                    <SelectItem key={v} value={v}>
                      {t(`geo.monitoringThemes.level.${v}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t("geo.monitoringThemes.edit.frequency")}</Label>
              <Select value={frequency} onValueChange={(v) => setFrequency(v as typeof frequency)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(["hourly", "daily", "weekly"] as const).map((v) => (
                    <SelectItem key={v} value={v}>
                      {t(`geo.monitoringThemes.freq.${v}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t("geo.monitoringThemes.edit.status")}</Label>
              <Select value={status} onValueChange={(v) => setStatus(v as typeof status)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(["active", "paused"] as const).map((v) => (
                    <SelectItem key={v} value={v}>
                      {t(`geo.monitoringThemes.status.${v}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>
        {update.isError && (
          <p className="text-sm text-destructive">{(update.error as Error).message}</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("geo.monitoringThemes.edit.cancel")}
          </Button>
          <Button onClick={save} disabled={update.isPending}>
            {update.isPending
              ? t("geo.monitoringThemes.edit.saving")
              : t("geo.monitoringThemes.edit.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
