import http from "k6/http";
import { check, sleep } from "k6";

const __expectedStatus = [200];
const __responseCallback = http.expectedStatuses(...__expectedStatus);
const __smoke = __ENV.QA_PERF_SMOKE === "true";
const __smokeVus = Number(__ENV.QA_PERF_SMOKE_VUS || 1);
const __smokeIterations = Number(__ENV.QA_PERF_SMOKE_ITERATIONS || 3);

const __loadOptions = {
  scenarios: {
    load: {
      executor: "ramping-arrival-rate",
      timeUnit: "1s",
      preAllocatedVUs: 1,
      maxVUs: 2000,
      stages: [
        { target: 25, duration: "0s" },
        { target: 50, duration: "0s" },
        { target: 50, duration: "0s" },
      ],
    },
  },
  thresholds: {
    "http_req_duration": [{ threshold: "p(99)<10000", abortOnFail: true, delayAbortEval: "10s" }],
    "http_req_failed": [{ threshold: "rate<0.01", abortOnFail: true, delayAbortEval: "10s" }],
  },
};

const __summaryTrendStats = ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"];
export const options = __smoke
  ? { vus: __smokeVus, iterations: __smokeIterations, summaryTrendStats: __summaryTrendStats }
  : { ...__loadOptions, summaryTrendStats: __summaryTrendStats };

export default function () {
  const __correlations = Object.create(null);
  const __headers0 = {"Content-Type": "application/json"};
  if (true) {
    const res0 = http.post("http://host.docker.internal:65031/json", JSON.stringify({"enabled": false, "count": 0, "nullable": null, "nested": ["\u4e2d\u6587", {"literal": "${__groovy(1+1)}", "extra": "keep"}]}), {
      headers: __headers0,
      responseCallback: __responseCallback,
    });

    check(res0, {
      ["qa_status_0_" + res0.status]: (r) => __expectedStatus.includes(r.status),
    });
    let __value0;
    let __parsed0 = false;
    try {
      __value0 = res0.json("matched");
      __parsed0 = true;
    } catch (_) {
      check(false, { "qa_json_parse_0": (v) => v === true });
    }
    const __extracted0 = __parsed0 && __expectedStatus.includes(res0.status) && __value0 !== undefined && __value0 !== null && __value0 !== "";
    check(__extracted0, { "qa_extract_0": (v) => v === true });
    if (__extracted0) __correlations[0] = __value0;
  }

  const __headers1 = {"Content-Type": "text/plain; charset=utf-8"};
  if (true) {
    const res1 = http.post("http://host.docker.internal:65031/text", "\u4e2d\u6587 ${1+1}\\path\nline", {
      headers: __headers1,
      responseCallback: __responseCallback,
    });

    check(res1, {
      ["qa_status_1_" + res1.status]: (r) => __expectedStatus.includes(r.status),
    });
  }

  const __headers2 = {"Content-Type": "text/plain; charset=utf-8"};
  if (true) {
    const res2 = http.put("http://host.docker.internal:65031/literal", "\u4e2d\u6587 ` ${1+1} ${__groovy(1+1)} \\path\r\n\t\u0000end\ud83d\ude00", {
      headers: __headers2,
      responseCallback: __responseCallback,
    });

    check(res2, {
      ["qa_status_2_" + res2.status]: (r) => __expectedStatus.includes(r.status),
    });
  }

  const __headers3 = {"Content-Type": "application/json"};
  if (true) {
    const res3 = http.patch("http://host.docker.internal:65031/patch", JSON.stringify({"unknown": {"items": [false, 0, null, "\ud83d\ude00\r\n"]}}), {
      headers: __headers3,
      responseCallback: __responseCallback,
    });

    check(res3, {
      ["qa_status_3_" + res3.status]: (r) => __expectedStatus.includes(r.status),
    });
  }

  const __headers4 = {"Content-Type": "text/plain; charset=utf-8"};
  if (true) {
    const res4 = http.post("http://host.docker.internal:65031/empty", "", {
      headers: __headers4,
      responseCallback: __responseCallback,
    });

    check(res4, {
      ["qa_status_4_" + res4.status]: (r) => __expectedStatus.includes(r.status),
    });
  }

  const __headers5 = {"Content-Type": "text/plain; charset=utf-8"};
  if (true) {
    const res5 = http.put("http://host.docker.internal:65031/none", null, {
      headers: __headers5,
      responseCallback: __responseCallback,
    });

    check(res5, {
      ["qa_status_5_" + res5.status]: (r) => __expectedStatus.includes(r.status),
    });
  }

  const __headers6 = {"Content-Type": "text/plain; charset=utf-8"};
  const __bound0 = __correlations[0];
  const __canBind0 = ["string", "number", "boolean"].includes(typeof __bound0) && __bound0 !== "";
  check(__canBind0, { "qa_dependency_0": (v) => v === true });
  if (__canBind0) __headers6["X-Match"] = "" + String(__bound0);
  if (__canBind0) {
    const res6 = http.post("http://host.docker.internal:65031/dependent", "\u6b63\u6587\u4e0e\u5f53\u8f6e\u4f9d\u8d56\u540c\u65f6\u5b58\u5728 ${1+1}", {
      headers: __headers6,
      responseCallback: __responseCallback,
    });

    check(res6, {
      ["qa_status_6_" + res6.status]: (r) => __expectedStatus.includes(r.status),
    });
  }

  sleep(0.025);
}