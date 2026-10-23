import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2, Paperclip, Plus } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { AiStatusBadge, DisplayNameBox, ErrorNote, LogFormDialog, UserNames } from "@/components/geo/external-network";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useExtContacts, useExtLogs, useExtUsers } from "@/lib/geoExternalNetworkApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/external-network/logs/")({
  component: ExternalLogsPage,
});

const ALL = "__all__";

function ExternalLogsPage() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [contact, setContact] = useState(ALL);
  const [participant, setParticipant] = useState(ALL);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const usersQuery = useExtUsers();
  const me = usersQuery.data?.me;
  const contactsQuery = useExtContacts({ include_inactive: true });
  const query = useExtLogs({
    q: q.trim() || undefined,
    contact_id: contact === ALL ? undefined : contact,
    participant_id: participant === ALL ? undefined : participant,
    date_from: from || undefined,
    date_to: to || undefined,
  });
  const items = query.data?.items ?? [];

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoExternalNetwork") }, { label: t("nav.geoExternalLogs") }]} />
      <main className="flex-1 max-w-[1200px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-editorial text-4xl">{t("extnet.logs.title")}</h1>
            <p className="text-sm text-muted-foreground mt-2">{t("extnet.logs.desc")}</p>
          </div>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4 mr-1" />
            {t("extnet.new.log")}
          </Button>
        </section>

        <DisplayNameBox />

        <section className="flex flex-wrap items-end gap-3">
          <Input className="max-w-xs" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("extnet.search.logs")} />
          <Select value={contact} onValueChange={setContact}>
            <SelectTrigger className="w-52">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>
                {t("extnet.filter.contact")}：{t("extnet.filter.all")}
              </SelectItem>
              {(contactsQuery.data?.items ?? []).map((c) => (
                <SelectItem key={c.contact_id} value={c.contact_id}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={participant} onValueChange={setParticipant}>
            <SelectTrigger className="w-52">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>
                {t("extnet.filter.participant")}：{t("extnet.filter.all")}
              </SelectItem>
              {(usersQuery.data?.items ?? []).map((u) => (
                <SelectItem key={u.user_id} value={u.user_id}>
                  {u.display_name || u.user_id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="space-y-0.5">
            <span className="text-[11px] text-muted-foreground">{t("extnet.filter.from")}</span>
            <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-40" />
          </div>
          <div className="space-y-0.5">
            <span className="text-[11px] text-muted-foreground">{t("extnet.filter.to")}</span>
            <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-40" />
          </div>
        </section>

        {query.isLoading && <Loader2 className="h-5 w-5 animate-spin" />}
        {query.error && <ErrorNote error={query.error} />}
        {!query.isLoading && !query.error && items.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("extnet.empty.logs")}</p>
        )}
        {items.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("extnet.col.date")}</TableHead>
                <TableHead>{t("extnet.col.contacts")}</TableHead>
                <TableHead>{t("extnet.col.interviewers")}</TableHead>
                <TableHead>{t("extnet.col.summary")}</TableHead>
                <TableHead>{t("extnet.col.ai")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((l) => (
                // 行のどこを押しても詳細が開く（日付の文字だけがリンクだと、メモを押しても開かないように見えた）
                <TableRow
                  key={l.log_id}
                  className="cursor-pointer hover:bg-muted/40"
                  onClick={() =>
                    navigate({
                      to: "/geo/external-network/logs/$logId",
                      params: { logId: l.log_id },
                    })
                  }
                >
                  <TableCell className="whitespace-nowrap font-medium">
                    <Link to="/geo/external-network/logs/$logId" params={{ logId: l.log_id }} className="hover:underline">
                      {l.contact_date}
                    </Link>
                  </TableCell>
                  <TableCell>{l.contacts.map((c) => c.name).join("、")}</TableCell>
                  <TableCell>
                    <UserNames ids={l.participant_ids} me={me} />
                  </TableCell>
                  <TableCell className="max-w-md">
                    <span className="line-clamp-2 text-sm">{l.summary_snippet ?? l.memo_head ?? "—"}</span>
                    {l.attachment_count > 0 && (
                      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <Paperclip className="h-3 w-3" />
                        {l.attachment_count}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <AiStatusBadge status={l.ai_status} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </main>
      <LogFormDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(logId) => navigate({ to: "/geo/external-network/logs/$logId", params: { logId } })}
      />
    </>
  );
}
