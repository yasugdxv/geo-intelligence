// External Network v1: 外部コンタクト / 面談ログ画面で共有する部品。
import { useEffect, useMemo, useState } from "react";
import { Download, Loader2, Paperclip, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useLanguage } from "@/lib/i18n";
import {
  downloadAttachment,
  useAddExtAttachment,
  useAdoptExtAi,
  useCreateExtLog,
  useDeleteExtAttachment,
  useExtContacts,
  useExtUsers,
  useRunExtAiSummary,
  useSaveExtAiEdit,
  useSaveExtContact,
  useSetMyDisplayName,
  useUpdateExtLog,
  userLabel,
  type ExtAiContent,
  type ExtAiItem,
  type ExtAiStatus,
  type ExtAttachment,
  type ExtContact,
  type ExtLog,
  type ExtUser,
} from "@/lib/geoExternalNetworkApi";

// ---- 添付の事前チェック（サーバー側でも必ず再検証される。ここは親切のための事前表示） ----
export const ATTACH_EXTENSIONS = [".pdf", ".docx", ".txt", ".pptx", ".xlsx"];
export const ATTACH_MAX_BYTES = 20 * 1024 * 1024;
export const ATTACH_MAX_COUNT = 10;

export function todayIso(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

export function ErrorNote({ error }: { error: unknown }) {
  const { t } = useLanguage();
  const msg = error instanceof Error ? error.message : String(error);
  const text = msg.includes("未作成") ? t("extnet.sqlNotApplied") : msg || t("extnet.loadError");
  return <p className="text-sm text-destructive">{text}</p>;
}

export function AiStatusBadge({ status }: { status: ExtAiStatus }) {
  const { t } = useLanguage();
  const variant = status === "failed" ? "destructive" : status === "completed" ? "default" : "outline";
  return <Badge variant={variant}>{t(`extnet.ai.status.${status}`)}</Badge>;
}

// ---- 複数選択（チェックボックス一覧。隠れたUIにしない） ----
function CheckList({
  options,
  value,
  onChange,
  empty,
}: {
  options: { id: string; label: string; sub?: string }[];
  value: string[];
  onChange: (v: string[]) => void;
  empty?: string;
}) {
  const [filter, setFilter] = useState("");
  const shown = options.filter((o) => `${o.label} ${o.sub ?? ""}`.toLowerCase().includes(filter.toLowerCase()));
  const toggle = (id: string) =>
    onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);
  return (
    <div className="space-y-1">
      {options.length > 6 && <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="…" />}
      <div className="border border-input rounded-md max-h-40 overflow-y-auto divide-y divide-border">
        {shown.length === 0 && <p className="px-3 py-2 text-xs text-muted-foreground">{empty ?? "—"}</p>}
        {shown.map((o) => (
          <label key={o.id} className="flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer hover:bg-accent/40">
            <input type="checkbox" checked={value.includes(o.id)} onChange={() => toggle(o.id)} />
            <span>{o.label}</span>
            {o.sub && <span className="text-xs text-muted-foreground truncate">{o.sub}</span>}
          </label>
        ))}
      </div>
    </div>
  );
}

export function UserMultiSelect({
  value,
  onChange,
  users,
  me,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  users: ExtUser[];
  me?: string;
}) {
  const { t } = useLanguage();
  return (
    <div className="space-y-1">
      <CheckList
        options={users.map((u) => ({
          id: u.user_id,
          label: (u.display_name || u.user_id) + (u.user_id === me ? ` ${t("extnet.me")}` : ""),
          // 表示名が無い人は内部IDで出る。表示名がある人は、同姓同名の見分け用に権限を添える
          sub: u.display_name ? (u.role ?? undefined) : undefined,
        }))}
        value={value}
        onChange={onChange}
      />
      {users.some((u) => !u.display_name) && <p className="text-[11px] text-muted-foreground">{t("extnet.userNote")}</p>}
    </div>
  );
}

export function UserNames({ ids, me }: { ids: string[]; me?: string }) {
  const { t } = useLanguage();
  const users = useExtUsers().data?.items;
  return <>{ids.map((i) => userLabel(users, i) + (i === me ? ` ${t("extnet.me")}` : "")).join("、") || "—"}</>;
}

/** 自分の表示名の設定。未設定のときは目立たせ、設定済みのときは小さく表示して変更できる。 */
export function DisplayNameBox() {
  const { t } = useLanguage();
  const usersQuery = useExtUsers();
  const me = usersQuery.data?.me;
  const current = usersQuery.data?.items.find((u) => u.user_id === me)?.display_name ?? "";
  const save = useSetMyDisplayName();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  if (!me) return null;
  const unset = !current;
  if (!unset && !editing) {
    return (
      <p className="text-xs text-muted-foreground">
        {t("extnet.displayName.current", { name: current })}{" "}
        <button type="button" className="underline" onClick={() => { setName(current); setEditing(true); }}>
          {t("extnet.action.edit")}
        </button>
      </p>
    );
  }
  return (
    <div className="border border-border rounded-md px-4 py-3 bg-muted/40 space-y-2">
      <p className="text-sm">{unset ? t("extnet.displayName.prompt") : t("extnet.displayName.edit")}</p>
      <div className="flex gap-2 max-w-md">
        <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={50} placeholder={t("extnet.displayName.placeholder")} />
        <Button
          disabled={!name.trim() || save.isPending}
          onClick={() => save.mutate(name, { onSuccess: () => setEditing(false) })}
        >
          {save.isPending ? t("extnet.action.saving") : t("extnet.action.save")}
        </Button>
        {!unset && (
          <Button variant="outline" onClick={() => setEditing(false)}>
            {t("extnet.action.cancel")}
          </Button>
        )}
      </div>
      {save.error && <ErrorNote error={save.error} />}
    </div>
  );
}

// ---- Contact 作成・編集 ----
export function ContactFormDialog({
  open,
  onOpenChange,
  contact,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  contact?: ExtContact;
}) {
  const { t } = useLanguage();
  const usersQuery = useExtUsers();
  const me = usersQuery.data?.me;
  const save = useSaveExtContact();
  const [name, setName] = useState("");
  const [aff, setAff] = useState("");
  const [expertise, setExpertise] = useState("");
  const [memo, setMemo] = useState("");
  const [owners, setOwners] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(contact?.name ?? "");
    setAff(contact?.affiliation_title ?? "");
    setExpertise(contact?.expertise_note ?? "");
    setMemo(contact?.memo ?? "");
    setOwners(contact?.owner_ids ?? (me ? [me] : [])); // 新規は、ログイン中のユーザーが既定
    setError(null);
    save.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, contact?.contact_id, me]);

  const memoLocked = !!contact && contact.memo_hidden; // 見えないメモを空で上書きしない
  const submit = () => {
    if (!name.trim()) return setError(`${t("extnet.field.name")}：${t("extnet.required")}`);
    if (owners.length === 0) return setError(`${t("extnet.field.owners")}：${t("extnet.required")}`);
    setError(null);
    save.mutate(
      {
        contactId: contact?.contact_id,
        body: {
          name,
          affiliation_title: aff,
          owner_ids: owners,
          ...(memoLocked ? {} : { memo }),
          // 任意項目。新規で空欄なら送らない（SQL未適用の環境でも登録できるように）
          ...(contact || expertise.trim() ? { expertise_note: expertise } : {}),
        },
      },
      { onSuccess: () => onOpenChange(false) },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{contact ? t("extnet.action.edit") : t("extnet.new.contact")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>
              {t("extnet.field.name")} <span className="text-destructive text-xs">*</span>
            </Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
          </div>
          <div className="space-y-1">
            <Label>{t("extnet.field.affiliation")}</Label>
            <Input value={aff} onChange={(e) => setAff(e.target.value)} maxLength={300} />
          </div>
          <div className="space-y-1">
            <Label>{t("extnet.field.expertise")}</Label>
            <Textarea
              value={expertise}
              onChange={(e) => setExpertise(e.target.value)}
              rows={2}
              maxLength={2000}
              placeholder={t("extnet.field.expertisePlaceholder")}
            />
            <p className="text-[11px] text-muted-foreground">{t("extnet.field.expertiseHint")}</p>
          </div>
          <div className="space-y-1">
            <Label>
              {t("extnet.field.owners")} <span className="text-destructive text-xs">*</span>
            </Label>
            <UserMultiSelect value={owners} onChange={setOwners} users={usersQuery.data?.items ?? []} me={me} />
          </div>
          {!memoLocked && (
            <div className="space-y-1">
              <Label>{t("extnet.field.memo")}</Label>
              <Textarea value={memo} onChange={(e) => setMemo(e.target.value)} rows={4} />
            </div>
          )}
          {(error || save.error) && <ErrorNote error={error ?? save.error} />}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("extnet.action.cancel")}
          </Button>
          <Button onClick={submit} disabled={save.isPending}>
            {save.isPending ? t("extnet.action.saving") : t("extnet.action.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---- Contact Log 作成・編集 ----
export function LogFormDialog({
  open,
  onOpenChange,
  log,
  presetContactId,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  log?: ExtLog;
  presetContactId?: string;
  onCreated?: (logId: string) => void;
}) {
  const { t } = useLanguage();
  const usersQuery = useExtUsers();
  const me = usersQuery.data?.me;
  const contactsQuery = useExtContacts({});
  const create = useCreateExtLog();
  const update = useUpdateExtLog(log?.log_id ?? "");
  const [contactIds, setContactIds] = useState<string[]>([]);
  const [date, setDate] = useState(todayIso());
  const [participants, setParticipants] = useState<string[]>([]);
  const [memo, setMemo] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setContactIds(log ? log.contacts.map((c) => c.contact_id) : presetContactId ? [presetContactId] : []);
    setDate(log?.contact_date ?? todayIso()); // 面談日の既定は今日
    setParticipants(log?.participant_ids ?? (me ? [me] : [])); // 既定はログイン中のユーザー
    setMemo(log?.memo ?? "");
    setFiles([]);
    setError(null);
    create.reset();
    update.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, log?.log_id, presetContactId, me]);

  // 編集時は、既に紐づいている（無効化済みを含む）コンタクトも選択肢に残す
  const contactOptions = useMemo(() => {
    const active = (contactsQuery.data?.items ?? []).map((c) => ({
      id: c.contact_id,
      label: c.name,
      sub: c.affiliation_title ?? undefined,
    }));
    const known = new Set(active.map((a) => a.id));
    const extra = (log?.contacts ?? [])
      .filter((c) => !known.has(c.contact_id))
      .map((c) => ({ id: c.contact_id, label: c.name ?? c.contact_id, sub: c.affiliation_title ?? undefined }));
    return [...active, ...extra];
  }, [contactsQuery.data, log]);

  const onPickFiles = (list: FileList | null) => {
    if (!list) return;
    const next = [...files];
    for (const f of Array.from(list)) {
      const ext = `.${(f.name.split(".").pop() ?? "").toLowerCase()}`;
      if (!ATTACH_EXTENSIONS.includes(ext)) return setError(t("extnet.attach.errorExt", { name: f.name }));
      if (f.size > ATTACH_MAX_BYTES) return setError(t("extnet.attach.errorSize", { name: f.name }));
      if (next.length >= ATTACH_MAX_COUNT) return setError(t("extnet.attach.errorCount"));
      next.push(f);
    }
    setError(null);
    setFiles(next);
  };

  const pending = create.isPending || update.isPending;
  const submit = () => {
    if (contactIds.length === 0) return setError(`${t("extnet.field.contacts")}：${t("extnet.required")}`);
    if (participants.length === 0) return setError(`${t("extnet.field.participants")}：${t("extnet.required")}`);
    const hasAttachment = files.length > 0 || (log?.attachment_count ?? 0) > 0;
    if (!memo.trim() && !hasAttachment) return setError(t("extnet.logMemoHint"));
    setError(null);
    if (log) {
      update.mutate(
        { contact_ids: contactIds, contact_date: date, participant_ids: participants, memo },
        { onSuccess: () => onOpenChange(false) },
      );
    } else {
      create.mutate(
        { contact_ids: contactIds, contact_date: date, participant_ids: participants, memo, files },
        {
          onSuccess: (created) => {
            onOpenChange(false);
            onCreated?.(created.log_id);
          },
        },
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{log ? t("extnet.action.editLog") : t("extnet.new.log")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>
              {t("extnet.field.contacts")} <span className="text-destructive text-xs">*</span>
            </Label>
            <CheckList options={contactOptions} value={contactIds} onChange={setContactIds} />
          </div>
          <div className="space-y-1">
            <Label>{t("extnet.field.date")}</Label>
            <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>
              {t("extnet.field.participants")} <span className="text-destructive text-xs">*</span>
            </Label>
            <UserMultiSelect value={participants} onChange={setParticipants} users={usersQuery.data?.items ?? []} me={me} />
          </div>
          <div className="space-y-1">
            <Label>{t("extnet.field.logMemo")}</Label>
            <Textarea value={memo} onChange={(e) => setMemo(e.target.value)} rows={8} />
            <p className="text-[11px] text-muted-foreground">{t("extnet.logMemoHint")}</p>
          </div>
          {!log && (
            <div className="space-y-1">
              <Label>{t("extnet.field.attachments")}</Label>
              <Input
                type="file"
                multiple
                accept={ATTACH_EXTENSIONS.join(",")}
                onChange={(e) => {
                  onPickFiles(e.target.files);
                  e.target.value = "";
                }}
              />
              <p className="text-[11px] text-muted-foreground">{t("extnet.attach.conditions")}</p>
              {files.map((f, i) => (
                <div key={`${f.name}-${i}`} className="flex items-center gap-2 text-sm">
                  <Paperclip className="h-3.5 w-3.5" />
                  <span className="truncate">{f.name}</span>
                  <span className="text-xs text-muted-foreground">{formatBytes(f.size)}</span>
                  <button type="button" className="ml-auto" onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
          {(error || create.error || update.error) && <ErrorNote error={error ?? create.error ?? update.error} />}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("extnet.action.cancel")}
          </Button>
          <Button onClick={submit} disabled={pending}>
            {pending ? t("extnet.action.saving") : t("extnet.action.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---- 添付一覧 ----
export function AttachmentList({ log }: { log: ExtLog }) {
  const { t } = useLanguage();
  const add = useAddExtAttachment(log.log_id);
  const del = useDeleteExtAttachment(log.log_id);
  const [error, setError] = useState<string | null>(null);
  const atts = log.attachments ?? [];

  const onPick = (list: FileList | null) => {
    const f = list?.[0];
    if (!f) return;
    const ext = `.${(f.name.split(".").pop() ?? "").toLowerCase()}`;
    if (!ATTACH_EXTENSIONS.includes(ext)) return setError(t("extnet.attach.errorExt", { name: f.name }));
    if (f.size > ATTACH_MAX_BYTES) return setError(t("extnet.attach.errorSize", { name: f.name }));
    if (atts.length >= ATTACH_MAX_COUNT) return setError(t("extnet.attach.errorCount"));
    setError(null);
    add.mutate(f);
  };
  const onDownload = (a: ExtAttachment) => downloadAttachment(log.log_id, a).catch((e) => setError(String(e.message ?? e)));

  return (
    <div className="space-y-2">
      {atts.length === 0 && <p className="text-sm text-muted-foreground">—</p>}
      {atts.map((a) => (
        <div key={a.attachment_id} className="flex items-center gap-2 text-sm border border-border rounded-md px-3 py-2">
          <Paperclip className="h-4 w-4 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="truncate">{a.filename}</div>
            <div className="text-xs text-muted-foreground">
              {formatBytes(a.size_bytes)} ・ {t(`extnet.attach.extract.${a.extraction_status}`)}
            </div>
          </div>
          <Button size="sm" variant="outline" onClick={() => onDownload(a)}>
            <Download className="h-3.5 w-3.5 mr-1" />
            {t("extnet.attach.download")}
          </Button>
          {log.can_edit && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => window.confirm(t("extnet.attach.deleteConfirm")) && del.mutate(a.attachment_id)}
              aria-label={t("extnet.attach.delete")}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      ))}
      {log.can_edit && (
        <div className="space-y-1">
          <Input type="file" accept={ATTACH_EXTENSIONS.join(",")} onChange={(e) => { onPick(e.target.files); e.target.value = ""; }} disabled={add.isPending} />
          <p className="text-[11px] text-muted-foreground">{t("extnet.attach.conditions")}</p>
        </div>
      )}
      {(error || add.error || del.error) && <ErrorNote error={error ?? add.error ?? del.error} />}
    </div>
  );
}

// ---- AI整理（表示・実行・編集） ----
const EMPTY_CONTENT: ExtAiContent = {
  summary: "",
  key_takeaways: [],
  expert_views: [],
  supporting_points: [],
  weakening_points: [],
  watchpoints: [],
  related_context: { countries: [], topics: [], actors: [], situation_candidates: [] },
  related_context_status: "unconfirmed",
  notice: "",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <h4 className="text-sm font-semibold">{title}</h4>
      {children}
    </div>
  );
}

function ItemView({ item, showType }: { item: ExtAiItem; showType?: boolean }) {
  const { t } = useLanguage();
  return (
    <li className="text-sm border border-border rounded-md px-3 py-2 space-y-1">
      <div className="flex flex-wrap items-center gap-1.5">
        {showType && item.type && <Badge variant={item.type === "unverified" ? "outline" : "secondary"}>{t(`extnet.ai.type.${item.type}`)}</Badge>}
        <span className="text-xs text-muted-foreground">
          {t("extnet.ai.speaker")}：{item.speaker ?? t("extnet.ai.speakerUnknown")}
        </span>
        {item.edited_by_human && <Badge variant="outline">{t("extnet.ai.source.edited")}</Badge>}
      </div>
      <p>{item.text}</p>
      {item.source_quote && (
        <p className="text-xs text-muted-foreground">
          {t("extnet.ai.quote")}：「{item.source_quote}」
          {item.quote_verified === false && <span className="text-destructive ml-1">（{t("extnet.ai.quoteUnverified")}）</span>}
        </p>
      )}
    </li>
  );
}

function ItemsEditor({ items, onChange }: { items: ExtAiItem[]; onChange: (v: ExtAiItem[]) => void }) {
  return (
    <div className="space-y-1.5">
      {items.map((it, i) => (
        <div key={i} className="flex items-start gap-2">
          <Textarea
            rows={2}
            value={it.text}
            onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
          />
          <Button type="button" size="sm" variant="ghost" onClick={() => onChange(items.filter((_, j) => j !== i))}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      ))}
    </div>
  );
}

const lines = (v: string[]) => v.join("\n");
const splitLines = (v: string) => v.split("\n").map((s) => s.trim()).filter(Boolean);

function AiEditor({ log, initial, onClose }: { log: ExtLog; initial: ExtAiContent; onClose: () => void }) {
  const { t } = useLanguage();
  const save = useSaveExtAiEdit(log.log_id);
  const [c, setC] = useState<ExtAiContent>({ ...EMPTY_CONTENT, ...initial });
  const ctx = c.related_context;
  const setCtx = (k: keyof ExtAiContent["related_context"], v: string) =>
    setC({ ...c, related_context: { ...ctx, [k]: v.split(/[,、\n]/).map((s) => s.trim()).filter(Boolean) } });

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">{t("extnet.ai.editHint")}</p>
      <Section title={t("extnet.ai.summary")}>
        <Textarea rows={4} value={c.summary} onChange={(e) => setC({ ...c, summary: e.target.value })} />
      </Section>
      <Section title={t("extnet.ai.takeaways")}>
        <Textarea rows={4} value={lines(c.key_takeaways)} onChange={(e) => setC({ ...c, key_takeaways: splitLines(e.target.value) })} />
      </Section>
      <Section title={t("extnet.ai.views")}>
        <ItemsEditor items={c.expert_views} onChange={(v) => setC({ ...c, expert_views: v })} />
      </Section>
      <Section title={t("extnet.ai.supporting")}>
        <ItemsEditor items={c.supporting_points} onChange={(v) => setC({ ...c, supporting_points: v })} />
      </Section>
      <Section title={t("extnet.ai.weakening")}>
        <ItemsEditor items={c.weakening_points} onChange={(v) => setC({ ...c, weakening_points: v })} />
      </Section>
      <Section title={t("extnet.ai.watchpoints")}>
        <Textarea
          rows={3}
          value={lines(c.watchpoints.map((w) => w.text))}
          onChange={(e) => setC({ ...c, watchpoints: splitLines(e.target.value).map((text) => ({ text, explicit: true })) })}
        />
      </Section>
      <Section title={t("extnet.ai.context")}>
        {(
          [
            ["countries", "extnet.ai.country"],
            ["topics", "extnet.ai.topic"],
            ["actors", "extnet.ai.actor"],
            ["situation_candidates", "extnet.ai.situation"],
          ] as const
        ).map(([k, label]) => (
          <div key={k} className="space-y-0.5">
            <Label className="text-xs">{t(label)}</Label>
            <Input defaultValue={ctx[k].join("、")} onBlur={(e) => setCtx(k, e.target.value)} />
          </div>
        ))}
      </Section>
      {save.error && <ErrorNote error={save.error} />}
      <div className="flex gap-2">
        <Button onClick={() => save.mutate(c, { onSuccess: onClose })} disabled={save.isPending || !c.summary.trim()}>
          {save.isPending ? t("extnet.action.saving") : t("extnet.action.save")}
        </Button>
        <Button variant="outline" onClick={onClose}>
          {t("extnet.action.cancel")}
        </Button>
      </div>
    </div>
  );
}

export function AiSummaryPanel({ log }: { log: ExtLog }) {
  const { t } = useLanguage();
  const run = useRunExtAiSummary(log.log_id);
  const adopt = useAdoptExtAi(log.log_id);
  const [editing, setEditing] = useState(false);
  const s = log.ai_summary;
  const content = s?.content ?? null;
  const running = run.isPending || log.ai_status === "running";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <AiStatusBadge status={log.ai_status} />
        {s?.source && <Badge variant="outline">{t(`extnet.ai.source.${s.source}`)}</Badge>}
        {log.can_edit && !editing && (
          <Button size="sm" variant="outline" onClick={() => run.mutate()} disabled={running}>
            {running ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : null}
            {running ? t("extnet.ai.running") : content ? t("extnet.ai.rerun") : t("extnet.ai.run")}
          </Button>
        )}
        {log.can_edit && content && !editing && (
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            {t("extnet.ai.edit")}
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{t("extnet.ai.notice")}</p>
      {log.ai_status === "failed" && <p className="text-sm text-destructive">{t("extnet.ai.failedNote")}</p>}
      {run.error && <ErrorNote error={run.error} />}
      {adopt.error && <ErrorNote error={adopt.error} />}
      {s?.ai_updated_after_edit && (
        <div className="text-sm border border-border rounded-md px-3 py-2 space-y-1.5 bg-muted/40">
          <p>{t("extnet.ai.updatedAfterEdit")}</p>
          {log.can_edit && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => window.confirm(t("extnet.ai.adoptConfirm")) && adopt.mutate()}
              disabled={adopt.isPending}
            >
              {t("extnet.ai.adopt")}
            </Button>
          )}
        </div>
      )}

      {editing && content ? (
        <AiEditor log={log} initial={content} onClose={() => setEditing(false)} />
      ) : content ? (
        <div className="space-y-4">
          <Section title={t("extnet.ai.summary")}>
            <p className="text-sm whitespace-pre-wrap">{content.summary}</p>
          </Section>
          <Section title={t("extnet.ai.takeaways")}>
            {content.key_takeaways.length ? (
              <ul className="list-disc pl-5 text-sm space-y-0.5">
                {content.key_takeaways.map((k, i) => (
                  <li key={i}>{k}</li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">{t("extnet.ai.noItems")}</p>
            )}
          </Section>
          <Section title={t("extnet.ai.views")}>
            {content.expert_views.length ? (
              <ul className="space-y-1.5">
                {content.expert_views.map((v, i) => (
                  <ItemView key={i} item={v} showType />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">{t("extnet.ai.noItems")}</p>
            )}
          </Section>
          <Section title={t("extnet.ai.supporting")}>
            {content.supporting_points.length ? (
              <ul className="space-y-1.5">
                {content.supporting_points.map((v, i) => (
                  <ItemView key={i} item={v} />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">{t("extnet.ai.noItems")}</p>
            )}
          </Section>
          <Section title={t("extnet.ai.weakening")}>
            {content.weakening_points.length ? (
              <ul className="space-y-1.5">
                {content.weakening_points.map((v, i) => (
                  <ItemView key={i} item={v} />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">{t("extnet.ai.noItems")}</p>
            )}
          </Section>
          <Section title={t("extnet.ai.watchpoints")}>
            {content.watchpoints.length ? (
              <ul className="list-disc pl-5 text-sm space-y-0.5">
                {content.watchpoints.map((w, i) => (
                  <li key={i}>{w.text}</li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">{t("extnet.ai.noItems")}</p>
            )}
          </Section>
          <Section title={t("extnet.ai.context")}>
            <p className="text-[11px] text-muted-foreground">{t("extnet.ai.contextNote")}</p>
            <div className="flex flex-wrap gap-1.5">
              {(
                [
                  ["countries", "extnet.ai.country"],
                  ["topics", "extnet.ai.topic"],
                  ["actors", "extnet.ai.actor"],
                  ["situation_candidates", "extnet.ai.situation"],
                ] as const
              ).flatMap(([k, label]) =>
                content.related_context[k].map((v) => (
                  <Badge key={`${k}-${v}`} variant="outline">
                    {t(label)}：{v}
                  </Badge>
                )),
              )}
            </div>
          </Section>
        </div>
      ) : (
        !running && log.ai_status !== "failed" && <p className="text-sm text-muted-foreground">{t("extnet.ai.status.not_run")}</p>
      )}
    </div>
  );
}
