import * as React from "react";

import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";

/**
 * Geopolitical Intelligence Platformデザイン(Geopoloticaldashboard_design.html)の
 * .card相当。shadcn Cardをベースに、geo-cardユーティリティ(styles.css)で
 * モックアップの淡いシャドウ・ボーダーを適用する薄いラッパー。
 */
const GeoCard = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <Card ref={ref} className={cn("geo-card", className)} {...props} />
  ),
);
GeoCard.displayName = "GeoCard";

const GeoCardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement> & {
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
}>(({ className, eyebrow, title, meta, actions, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("flex items-start justify-between gap-3 px-5 pt-5 pb-3", className)}
    {...props}
  >
    <div className="min-w-0">
      {eyebrow && (
        <div className="geo-meta uppercase tracking-[0.12em] mb-1">
          {eyebrow}
        </div>
      )}
      <h2 className="geo-heading text-[19px] text-foreground">{title}</h2>
      {meta && <p className="geo-meta mt-0.5">{meta}</p>}
    </div>
    {actions && <div className="flex items-center gap-2 flex-shrink-0">{actions}</div>}
  </div>
));
GeoCardHeader.displayName = "GeoCardHeader";

const GeoCardBody = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <CardContent ref={ref} className={cn("px-5 pb-5 pt-0", className)} {...props} />
  ),
);
GeoCardBody.displayName = "GeoCardBody";

export { GeoCard, GeoCardHeader, GeoCardBody };
