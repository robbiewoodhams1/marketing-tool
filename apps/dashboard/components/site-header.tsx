"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { NAV_GROUPS } from "@/lib/nav";

// href -> label for every route the sidebar knows about, so the breadcrumb
// says "Insights" rather than "research / insights". A route this doesn't
// know about (mainly a dynamic [id] segment) falls back to a humanised
// version of the raw segment - honest, if less pretty, rather than guessing.
const LABELS = new Map(
  NAV_GROUPS.flatMap((g) => g.items).map((item) => [item.href, item.label]),
);

function humanise(segment: string): string {
  const decoded = decodeURIComponent(segment);
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(decoded)) return `${decoded.slice(0, 8)}…`;
  return decoded.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function crumbsFor(pathname: string): { href: string; label: string }[] {
  if (pathname === "/") return [{ href: "/", label: "Overview" }];
  const segments = pathname.split("/").filter(Boolean);
  let href = "";
  return segments.map((segment) => {
    href += `/${segment}`;
    return { href, label: LABELS.get(href) ?? humanise(segment) };
  });
}

export function SiteHeader() {
  const pathname = usePathname();
  const crumbs = crumbsFor(pathname);

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
      <SidebarTrigger />
      <Separator orientation="vertical" className="mr-2 h-4" />
      <Breadcrumb>
        <BreadcrumbList>
          {crumbs.map((crumb, index) => (
            <span key={crumb.href} className="flex items-center gap-1.5">
              {index > 0 && <BreadcrumbSeparator />}
              <BreadcrumbItem>
                {index === crumbs.length - 1 ? (
                  <BreadcrumbPage>{crumb.label}</BreadcrumbPage>
                ) : (
                  <BreadcrumbLink render={<Link href={crumb.href} />}>{crumb.label}</BreadcrumbLink>
                )}
              </BreadcrumbItem>
            </span>
          ))}
        </BreadcrumbList>
      </Breadcrumb>
    </header>
  );
}
