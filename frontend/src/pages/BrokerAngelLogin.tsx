import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { angelLogin } from "@/api/broker";

export default function BrokerAngelLogin() {
  const [clientcode, setClientcode] = useState("");
  const [brokerPin, setBrokerPin] = useState("");
  const [totp, setTotp] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const loginMutation = useMutation({
    mutationFn: () =>
      angelLogin({
        clientcode: clientcode.trim(),
        broker_pin: brokerPin.trim(),
        totp_code: totp.trim(),
      }),
    onSuccess: () => {
      navigate("/dashboard");
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      setError(axiosErr.response?.data?.detail ?? "Angel One authentication failed.");
    },
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (!clientcode.trim() || !brokerPin.trim() || !totp.trim()) {
      setError("Client Code, MPIN and TOTP are all required.");
      return;
    }
    loginMutation.mutate();
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Login with Angel One</CardTitle>
          <CardDescription>
            Angel One SmartAPI uses your trading credentials with TOTP.
            Enter your Client Code, MPIN and current TOTP from your authenticator app.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="clientcode">Client Code</Label>
              <Input
                id="clientcode"
                type="text"
                value={clientcode}
                onChange={(e) => setClientcode(e.target.value)}
                placeholder="e.g. A123456"
                autoComplete="username"
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="broker-pin">MPIN</Label>
              <Input
                id="broker-pin"
                type="password"
                value={brokerPin}
                onChange={(e) => setBrokerPin(e.target.value)}
                placeholder="4-digit MPIN"
                autoComplete="current-password"
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="totp">TOTP Code</Label>
              <Input
                id="totp"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={totp}
                onChange={(e) => setTotp(e.target.value)}
                placeholder="6-digit code"
                maxLength={6}
                required
              />
              <p className="text-xs text-muted-foreground">
                From your Angel SmartAPI authenticator app (Google Authenticator, Authy etc.).
              </p>
            </div>

            <Button type="submit" className="w-full" disabled={loginMutation.isPending}>
              {loginMutation.isPending ? "Authenticating..." : "Login"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
