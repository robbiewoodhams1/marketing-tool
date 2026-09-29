import { Send } from "lucide-react";
import { ComingNext } from "@/components/coming-next";

export default function DistributionPage() {
  return (
    <main className="flex-1 space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Distribution</h1>
        <p className="mt-1 text-muted-foreground">Publishing generated media to real platforms.</p>
      </div>
      <ComingNext
        icon={Send}
        title="Publishing infrastructure is next"
        description="Once media assets are generated and approved, this is where they get scheduled and published to TikTok, Instagram and YouTube."
      />
    </main>
  );
}
