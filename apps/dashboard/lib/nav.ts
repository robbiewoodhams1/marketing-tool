import {
  Boxes,
  Clapperboard,
  FileText,
  Flag,
  Lightbulb,
  LineChart,
  LayoutDashboard,
  MessageSquare,
  Rocket,
  Send,
  Settings,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

// The pipeline this whole application is organised around. Order here is the
// order the workflow actually happens in, and drives both the sidebar's
// MARKETING group and the Overview page's pipeline strip - one source of
// truth so the two never drift apart.
export type PipelineStageId =
  | "research"
  | "insights"
  | "opportunities"
  | "production"
  | "media"
  | "distribution"
  | "performance";

export type PipelineStage = {
  id: PipelineStageId;
  label: string;
  href: string;
  icon: LucideIcon;
  implemented: boolean; // false: shown honestly as "Coming next", no fabricated data
};

export const PIPELINE: PipelineStage[] = [
  { id: "research", label: "Research", href: "/research", icon: FileText, implemented: true },
  { id: "insights", label: "Insights", href: "/research/insights", icon: Lightbulb, implemented: true },
  { id: "opportunities", label: "Opportunities", href: "/research/opportunities", icon: Sparkles, implemented: true },
  { id: "production", label: "Production", href: "/research/production", icon: Clapperboard, implemented: true },
  { id: "media", label: "Media", href: "/research/media", icon: Boxes, implemented: true },
  { id: "distribution", label: "Distribution", href: "/research/distribution", icon: Send, implemented: false },
  { id: "performance", label: "Performance", href: "/research/performance", icon: LineChart, implemented: false },
];

export type NavItem = { label: string; href: string; icon: LucideIcon };
export type NavGroup = { label: string; items: NavItem[] };

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Marketing",
    items: [
      { label: "Overview", href: "/", icon: LayoutDashboard },
      ...PIPELINE.map((s) => ({ label: s.label, href: s.href, icon: s.icon })),
    ],
  },
  {
    label: "Data",
    items: [
      { label: "Content", href: "/research/content", icon: Boxes },
      { label: "Comments", href: "/research/comments", icon: MessageSquare },
      // Same underlying page as Marketing / Research: research_jobs is both
      // the start of the pipeline and the raw job data. Two entries to one
      // route, not a duplicate page - see the frontend-overhaul report.
      { label: "Research Jobs", href: "/research", icon: Flag },
    ],
  },
  {
    label: "System",
    items: [{ label: "Settings", href: "/settings", icon: Settings }],
  },
];

export const APP_NAME = "TradeFlow Marketing";
export { Rocket as AppIcon };
