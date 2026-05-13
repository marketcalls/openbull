import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { getStrategy } from "@/api/strategy_module";
import StrategyWizard from "./Wizard";

export default function StrategyEdit() {
  const { id } = useParams();
  const navigate = useNavigate();
  const sid = Number(id);

  const { data: strategy, isLoading, error } = useQuery({
    queryKey: ["strategy", sid],
    queryFn: () => getStrategy(sid),
    enabled: Number.isFinite(sid) && sid > 0,
  });

  if (isLoading) {
    return (
      <div className="py-12 text-center text-sm text-muted-foreground">
        Loading strategy…
      </div>
    );
  }

  if (error || !strategy) {
    return (
      <Card>
        <CardContent className="space-y-3 p-6 text-center">
          <p className="text-sm text-destructive">Failed to load strategy.</p>
          <Button variant="outline" onClick={() => navigate("/strategy")}>
            Back to list
          </Button>
        </CardContent>
      </Card>
    );
  }

  // Plan section 3.1: edits only allowed when the strategy is stopped.
  // The backend enforces this on PATCH too; the UI gate is just a clearer
  // explanation than seeing a 409 toast.
  if (strategy.status !== "stopped") {
    return (
      <Card>
        <CardContent className="space-y-3 p-6 text-center">
          <p className="text-sm">
            This strategy is currently{" "}
            <span className="font-mono">{strategy.status}</span>. Stop it
            before editing.
          </p>
          <Button
            variant="outline"
            onClick={() => navigate(`/strategy/${strategy.id}`)}
          >
            Back to detail
          </Button>
        </CardContent>
      </Card>
    );
  }

  return <StrategyWizard editing={strategy} />;
}
