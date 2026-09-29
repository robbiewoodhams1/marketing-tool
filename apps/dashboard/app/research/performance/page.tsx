import { LineChart } from "lucide-react";
import { ComingNext } from "@/components/coming-next";

export default function PerformancePage() {
  return (
    <main className="flex-1 space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Performance</h1>
        <p className="mt-1 text-muted-foreground">
          What worked, feeding back into the next round of research.
        </p>
      </div>
      <ComingNext
        icon={LineChart}
        title="Performance data will appear here once content is distributed"
        description="Views, engagement and conversion on published content will close the loop back into Research - what to look for next."
      />
    </main>
  );
}
