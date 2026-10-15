// Geopolitical Intelligence OS: Pilot-grade Identity Roster APIクライアント。
// バックエンドは/api/geo/ios/identities（geo_ios_identity_api.py）に接続する。
// credential自体はこのAPIから一切返らない（identity_id/role/domainのみ）。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useQuery } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers: { ...getIosAuthHeaders(), ...init?.headers } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

export interface GeoIosIdentity {
  identity_id: string;
  role: "viewer" | "analyst" | "expert_reviewer" | "knowledge_steward" | "approver" | "administrator";
  domain: string | null;
}

export function useIdentities(role?: string) {
  return useQuery({
    queryKey: ["geoIosIdentities", role ?? "all"],
    queryFn: () => fetchJson<{ identities: GeoIosIdentity[] }>(
      `/api/geo/ios/identities${role ? `?role=${encodeURIComponent(role)}` : ""}`),
  });
}
