import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ContactFormDialog, DisplayNameBox, ErrorNote, UserNames } from "@/components/geo/external-network";
import { useExtContacts, useExtUsers } from "@/lib/geoExternalNetworkApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/external-network/contacts/")({
  component: ExternalContactsPage,
});

const ALL = "__all__";

function ExternalContactsPage() {
  const { t } = useLanguage();
  const [q, setQ] = useState("");
  const [owner, setOwner] = useState(ALL);
  const [showInactive, setShowInactive] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const usersQuery = useExtUsers();
  const me = usersQuery.data?.me;
  const query = useExtContacts({
    q: q.trim() || undefined,
    owner_id: owner === ALL ? undefined : owner,
    include_inactive: showInactive,
  });
  const items = query.data?.items ?? [];

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoExternalNetwork") }, { label: t("nav.geoExternalContacts") }]} />
      <main className="flex-1 max-w-[1200px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-editorial text-4xl">{t("extnet.contacts.title")}</h1>
            <p className="text-sm text-muted-foreground mt-2">{t("extnet.contacts.desc")}</p>
          </div>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4 mr-1" />
            {t("extnet.new.contact")}
          </Button>
        </section>

        <DisplayNameBox />

        <section className="flex flex-wrap items-center gap-3">
          <Input className="max-w-xs" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("extnet.search.contacts")} />
          <Select value={owner} onValueChange={setOwner}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder={t("extnet.filter.owner")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>
                {t("extnet.filter.owner")}：{t("extnet.filter.all")}
              </SelectItem>
              {(usersQuery.data?.items ?? []).map((u) => (
                <SelectItem key={u.user_id} value={u.user_id}>
                  {u.display_name || u.user_id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
            {t("extnet.showInactive")}
          </label>
        </section>

        {query.isLoading && <Loader2 className="h-5 w-5 animate-spin" />}
        {query.error && <ErrorNote error={query.error} />}
        {!query.isLoading && !query.error && items.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("extnet.empty.contacts")}</p>
        )}
        {items.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("extnet.col.name")}</TableHead>
                <TableHead>{t("extnet.col.affiliation")}</TableHead>
                <TableHead>{t("extnet.col.owners")}</TableHead>
                <TableHead>{t("extnet.col.lastContact")}</TableHead>
                <TableHead className="text-right">{t("extnet.col.count")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((c) => (
                <TableRow key={c.contact_id}>
                  <TableCell className="font-medium">
                    <Link to="/geo/external-network/contacts/$contactId" params={{ contactId: c.contact_id }} className="hover:underline">
                      {c.name}
                    </Link>
                    {c.status === "inactive" && (
                      <Badge variant="secondary" className="ml-2">
                        {t("extnet.status.inactive")}
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>{c.affiliation_title ?? "—"}</TableCell>
                  <TableCell>
                    <UserNames ids={c.owner_ids} me={me} />
                  </TableCell>
                  <TableCell>{c.last_contact_at ?? "—"}</TableCell>
                  <TableCell className="text-right">{c.log_count}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </main>
      <ContactFormDialog open={createOpen} onOpenChange={setCreateOpen} />
    </>
  );
}
