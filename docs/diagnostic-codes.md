# Check Diagnostic Codes

DocMeThis Check emits native `DMT-*` diagnostic codes. This page lists the
codes currently emitted by Check. It does not include reserved codes or codes
emitted only by other DocMeThis components.

The [Taxonomy of Documentation Violations (TDV)](https://github.com/DocMeThis/tdv-registry)
provides the cross-tool semantic taxonomy. TDV is referenced here for context;
the tables below contain Check codes only.

## How To Read This Page

A profile sets the severity of an eligible diagnostic. It does not select the
symbols that Check examines. A code is emitted only when all applicable gates
allow it:

1. The profile gives the code a severity other than `disabled`.
2. The symbol type is included by `symbol-kinds`.
3. The symbol's effective visibility is included by `include-visibility`.
4. Additional gates, such as `property-accessors`, `dia`, path filters, or a
   diagnostic-specific option, allow the check.

## Default Symbol Selection

Unless overridden, Check uses this configuration:

```toml
profile = "standard"
dia = true
include-visibility = ["public"]
symbol-kinds = ["function", "method", "class"]
property-accessors = ["getter", "setter"]
```

Bold cells show the resulting default combination: selected kind, `public`,
and Standard severity. A bold code is eligible by default, not guaranteed to
produce a finding. Add modules, protected/private visibility, or other kinds
explicitly when needed.

In the tables, the `Symbol kinds` column uses the exact values accepted by
`symbol-kinds`. Comma-separated kinds are alternatives; `class + method` for
`DMT-4301` means that both selections are required. The `Visibility` column
uses the exact values accepted by `include-visibility`. `N.A.` means that the
setting does not apply.

These are the built-in Check profiles:

- **Loose** reports only contract changes likely to mislead readers. It does
  not report missing documentation or formatting findings.
- **Standard** covers normal documentation and contract quality. Formatting
  findings remain warnings.
- DIA findings remain warnings under Standard; Strict promotes them to errors.
- **Strict** keeps the same coverage as Standard but treats more contract
  gaps and behavior changes as errors.
- The profile levels are monotonic: a stricter profile only enables a
  diagnostic or raises its level from `disabled` to `warning` to `error`.
- `disabled` means that the profile does not emit the diagnostic, even if its
  symbol type and visibility are selected.
- **Fixable** indicates whether the finding is suitable for an
  automatic documentation patch with [DocMeThis Fix](https://docmethis.com/fr/ci-fix/start/) v0.1.0 —
  note that the purpose of DocMeThis Fix is reliability, so although many of the findings
  are on the DocMeThis Fix roadmap, some will likely remain unfixable forever because
  the AST cannot provide sufficiently reliable context to the LLM. And no, "all context"
  doesn't necessarily qualifies as "sufficiently reliable context".
- Whether a warning fails CI is controlled separately by `fail-on-warning`.
- Explicit values in `[tool.docmethis.check.severity]` override the profile.

## Diagnostic Codes

### 1xxx - Missing documentation

| Code | Meaning | Symbol kinds | Visibility | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `DMT-1101` | Public module has no docstring. | module | public | disabled | error | error | - |
| **`DMT-1110`** | Public class has no docstring. | **class** | **public** | disabled | **error** | error | - |
| **`DMT-1120`** | Public function has no docstring. | **function** | **public** | disabled | **error** | error | - |
| **`DMT-1130`** | Public method has no docstring. | **method** | **public** | disabled | **error** | error | - |
| **`DMT-1140`** | Public property has no docstring. | **method** | **public** | disabled | **error** | error | - |
| **`DMT-1150`** | Public attribute has no docstring. | **class** | **public** | disabled | **error** | error | - |
| `DMT-1201` | Protected module has no docstring. | module | protected | disabled | warning | error | - |
| `DMT-1210` | Protected class has no docstring. | class | protected | disabled | warning | error | - |
| `DMT-1220` | Protected function has no docstring. | function | protected | disabled | warning | error | - |
| `DMT-1230` | Protected method has no docstring. | method | protected | disabled | warning | error | - |
| `DMT-1240` | Protected property has no docstring. | method | protected | disabled | warning | error | - |
| `DMT-1250` | Protected attribute has no docstring. | class | protected | disabled | warning | error | - |
| `DMT-1301` | Private module has no docstring. | module | private | disabled | disabled | error | - |
| `DMT-1310` | Private class has no docstring. | class | private | disabled | disabled | error | - |
| `DMT-1320` | Private function has no docstring. | function | private | disabled | disabled | error | - |
| `DMT-1330` | Private method has no docstring. | method | private | disabled | disabled | error | - |
| `DMT-1340` | Private property has no docstring. | method | private | disabled | disabled | error | - |
| `DMT-1350` | Private attribute has no docstring. | class | private | disabled | disabled | error | - |

### 2xxx - Parameters

| Code | Meaning | Symbol kinds | Visibility | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **`DMT-2001`** | Parameter is undocumented. | **function**, **method**, **class** | **public**, protected, private | warning | **error** | error | ✓ |
| **`DMT-2002`** | Documented parameter is absent from the signature. | **function**, **method** | **public**, protected, private | error | **error** | error | ✓ |
| **`DMT-2003`** | Inconsistent parameter order. | **function**, **method** | **public**, protected, private | warning | **warning** | error | ✓ |
| **`DMT-2120`** | Parameter type is absent from the docstring. | **function**, **method** | **public**, protected, private | disabled | **warning** | error | ✓ |

### 3xxx - Produced values

| Code | Meaning | Symbol kinds | Visibility | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **`DMT-3001`** | Return value is undocumented. | **function**, **method** | **public**, protected, private | warning | **error** | error | ✓ |
| **`DMT-3010`** | Return type is absent from the docstring. | **function**, **method** | **public**, protected, private | disabled | **warning** | error | ✓ |

### 4xxx - Exceptions

| Code | Meaning | Symbol kinds | Visibility | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **`DMT-4001`** | Raised exception is undocumented. | **function**, **method** | **public**, protected, private | warning | **error** | error | ✓ |
| **`DMT-4002`** | Documented exception has no detected raise source. | **function**, **method** | **public**, protected, private | error | **error** | error | ✓ |
| **`DMT-4101`** | Missing description for an exception in Raises. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-4102`** | Invalid exception name in Raises. | **function**, **method** | **public**, protected, private | disabled | **warning** | error | - |
| **`DMT-4201`** | Exception exposed after a change is not aligned with Raises (DIA). | **function**, **method** | **public**, protected, private | error | **warning** | error | ✓ |
| **`DMT-4202`** | Documented exception removed from behavior (DIA). | **function**, **method** | **public**, protected, private | warning | **warning** | error | ✓ |
| `DMT-4301` | Raised exception is undocumented in a class-level aggregate. | class + method | public, protected, private | disabled | warning | error | - |

### 5xxx - Structured objects

| Code | Meaning | Symbol kinds | Visibility | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **`DMT-5201`** | Method origin or override changed without corresponding documentation (DIA). | **method** | **public**, protected, private | warning | **warning** | error | ✓ |
| **`DMT-5202`** | Inheritance changed without corresponding documentation (DIA). | **class** | **public**, protected, private | warning | **warning** | error | ✓ |

### 6xxx - Representation and docstyles

| Code | Meaning | Symbol kinds | Visibility | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **`DMT-6000`** | Inconsistent indentation. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-6049`** | Section is probably misspelled. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-6051`** | Missing blank line. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-6200`** | Incorrect underline length. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-6201`** | Unknown section. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-6202`** | Section order is invalid. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-6210`** | Missing colon in a parameter entry. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-6211`** | Empty parameter name. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-6212`** | Incorrect spacing after a colon. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-6218`** | Missing type for a parameter. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-6220`** | Returns type and description are reversed. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-6240`** | Raises type and description are on the same line. | **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |

### 7xxx - Quality and semantics

| Code | Meaning | Symbol kinds | Visibility | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **`DMT-7001`** | Missing or empty summary. | module, **class**, **function**, **method** | **public**, protected, private | disabled | **warning** | warning | - |
| **`DMT-7302`** | I/O effect changed without corresponding documentation (DIA). | **function**, **method** | **public**, protected, private | warning | **warning** | error | ✓ |
| **`DMT-7303`** | Observable property changed without corresponding documentation (DIA). | **function**, **method** | **public**, protected, private | warning | **warning** | error | ✓ |
