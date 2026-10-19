"""Join deterministic evidence, optional model assessments and paper state."""
from datetime import datetime, timezone
import json
from pathlib import Path

from .gate import canonical, evaluate
from .models import iso, number, utc


class PaperEngine:
    def __init__(self,ledger,*,jev=None,research=None,clock=None):
        self.ledger=ledger
        self.config=ledger.config
        self.jev=jev
        self.research=research
        self.clock=clock or (lambda:datetime.now(timezone.utc))

    def _assessment(self,name,call):
        try:
            result=call()
            if not isinstance(result,dict):raise ValueError("provider result is not an object")
            # Bound serialization before storing any untrusted model response.
            if len(canonical(result).encode())>65536:raise ValueError("provider result too large")
            if result.get("status") not in {"ok","unavailable","invalid"}:raise ValueError("unknown provider status")
            return result
        except Exception:
            return {"status":"unavailable","provider":name,"reason":"PROVIDER_FAILED"}

    def run(self,snapshot,*,now=None,executor="native"):
        now=utc(now or self.clock())
        initial=evaluate(snapshot,now=now,config=self.config,account=self.ledger.account(now))
        existing=self.ledger.decision(initial["decision_id"])
        if existing:
            if existing["executor"]!=executor:raise ValueError("decision already owned by a different paper executor")
            return existing
        decision=initial
        decision["assessments"]={"research":{"status":"disabled"},"jev":{"status":"disabled"}}
        if decision["call"] in {"LONG","SHORT"}:
            if self.config.research_mode!="disabled":
                if self.research is None or decision["provenance"]!="market":
                    research={"status":"unavailable","provider":"tradingagents","reason":"PROVIDER_NOT_CONFIGURED"}
                else:
                    research=self._assessment("tradingagents",lambda:self.research.research(
                        ticker=snapshot["symbol"],asset_type=snapshot["asset_class"],as_of=now))
                decision["assessments"]["research"]=research
                expected="bullish" if decision["call"]=="LONG" else "bearish"
                if research.get("status")=="ok":
                    try:
                        if research.get("symbol")!=snapshot["symbol"] or not now<=utc(research["as_of"])<=utc(self.clock()):
                            raise ValueError("research observation mismatch")
                    except (ValueError,KeyError,TypeError):
                        research={"status":"invalid","provider":"tradingagents","reason":"RESEARCH_IDENTITY_OR_TIME_MISMATCH"}
                        decision["assessments"]["research"]=research
                if self.config.research_mode=="required":
                    if research.get("status")!="ok" or research.get("stance")!=expected:
                        self._reject(decision,"RESEARCH_REQUIRED_UNAVAILABLE_OR_CONFLICTING")
            if self.config.jev_mode!="disabled" and decision["call"] in {"LONG","SHORT"}:
                intent=decision["intent"]
                # Numeric decisions are already made in code. No human identity,
                # holdings, credentials, raw calendar prose or research debates.
                state={"candidate_id":decision["decision_id"],"symbol":decision["symbol"],"side":decision["call"],
                       "provenance":decision["provenance"],"setup":"reviewed_major_level_breakout_and_later_retest",
                       "deterministic_checks_passed":True,"risk_reward_after_friction":intent["reward_risk"],
                       "spread_bps":decision["evidence"]["spread_bps"],
                       "timeframes_aligned":True,"calendar_checked":True,
                       "research_stance":decision["assessments"]["research"].get("stance","not_requested")}
                assessment=self._assessment("jev",lambda:self.jev.evaluate(state)) if self.jev else {
                    "status":"unavailable","provider":"jev","reason":"PROVIDER_NOT_CONFIGURED"}
                decision["assessments"]["jev"]=assessment
                if self.config.jev_mode=="required":
                    allowed=False
                    try:
                        confidence=number(assessment.get("confidence"),"confidence")
                        probabilities=assessment["probabilities"]
                        valid_probs=set(probabilities)=={"pass","reject"} and all(
                            0<=number(value,"probability")<=1 for value in probabilities.values())
                        allowed=(assessment.get("status")=="ok" and assessment.get("choice")=="pass" and
                                 self.config.jev_min_confidence<=confidence<=1 and valid_probs and
                                 abs(sum(probabilities.values())-1)<=0.001 and probabilities["pass"]>probabilities["reject"])
                    except (ValueError,TypeError,KeyError):pass
                    if not allowed:self._reject(decision,"JEV_REQUIRED_UNAVAILABLE" if assessment.get("status")!="ok" else "JEV_REQUIRED_REJECTED")
        # A synchronous provider call may outlive the observation. Use wall time
        # again for market evidence, while explicit replay uses its event clock.
        record_time=utc(self.clock()) if decision["provenance"]=="market" else now
        return self.ledger.record(snapshot,decision,now=record_time,executor=executor)

    @staticmethod
    def _reject(decision,reason):
        decision.update(call="NO_TRADE",intent=None)
        decision["reason_codes"].append(reason)

    def advance(self,instrument,bar,*,observed_at,provenance,timeframe="15m"):
        self.ledger.advance(instrument,bar,observed_at=observed_at,provenance=provenance,timeframe=timeframe)

    def export_freqtrade_ticket(self,decision,path,*,now=None):
        from .adapters.freqtrade import validate_ticket

        now=utc(now or self.clock())
        saved=self.ledger.decision(decision.get("decision_id"))
        if not saved or saved!=decision or saved.get("executor")!="freqtrade_dry_run":
            raise ValueError("only a persisted Freqtrade-owned approval can be exported")
        if decision["provenance"]!="market" or decision["call"]!="LONG" or decision["asset_class"]!="crypto":
            raise ValueError("Freqtrade boundary currently requires a market-sourced crypto spot LONG")
        if not utc(decision["approved_at"])<=now<utc(decision["expires_at"]):raise ValueError("paper approval is not current")
        intent=decision["intent"]
        ticket={"schema_version":1,"mode":"paper","executor":"freqtrade_dry_run","provenance":"market",
                "decision_id":decision["decision_id"],"symbol":decision["symbol"],"side":"LONG",
                "issued_at":decision["approved_at"],"expires_at":decision["expires_at"],
                "signal_bar_close":decision["evidence"]["retest"],"entry_tolerance_bps":10,
                **{key:intent[key] for key in ("entry","stop","target","quantity","notional","risk_cash","fee_bps","slippage_bps")}}
        # Use the same contract as the final consumer before creating any output.
        # General paper settings may permit a longer TTL or another symbol form.
        if not validate_ticket(ticket,pair=ticket["symbol"],side="long",rate=ticket["entry"],
                               amount=ticket["quantity"],now=now,dry_run=True,runmode="dry_run"):
            raise ValueError("approval does not satisfy the Freqtrade dry-run ticket contract")
        path=Path(path)
        if path.name!=decision["decision_id"]+".json":raise ValueError("ticket filename must match decision_id.json")
        path.parent.mkdir(parents=True,exist_ok=True)
        raw=canonical(ticket)+"\n"
        # Immutable create; a second writer must not replace an approved ticket.
        with path.open("x",encoding="utf-8") as handle:handle.write(raw)
        return ticket
