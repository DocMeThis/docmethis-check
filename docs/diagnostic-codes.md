# Check Diagnostic Codes

DocMeThis Check emits native `DMT-*` diagnostic codes. This page lists the
codes currently emitted by Check. It does not include reserved codes or codes
emitted only by other DocMeThis components.

The [Taxonomy of Documentation Violations (TDV)](https://github.com/DocMeThis/tdv-registry)
provides the cross-tool semantic taxonomy. TDV is referenced here for context;
the table below contains Check codes only.

## Check Profile Matrix

These are the built-in Check profiles. `standard` is selected by default.

- **Loose** reports only contract changes likely to mislead readers. It does
  not report missing documentation or formatting findings.
- **Standard** covers normal documentation and contract quality. Formatting
  findings remain warnings.
- **Strict** keeps the same coverage as Standard but treats more contract
  gaps and behavior changes as errors.
- The profile levels are monotonic: a stricter profile only enables a
  diagnostic or raises its level from `disabled` to `warning` to `error`.
- `disabled` means that the diagnostic is not reported.
- **Fixable** uses `✓` when the registry marks the finding as suitable for an
  automatic documentation patch. `-` means that it is not marked fixable.
- Whether a warning fails CI is controlled separately by `fail_on_warning`.
- The intermediate headings follow the official TDV-PY top-level headings. They
  are navigation aids, not a separate DMT taxonomy.

## Diagnostic Codes

### 1xxx - Missing documentation

| Code | Meaning | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- |
| `DMT-1101` | Public module has no docstring. | disabled | error | error | - |
| `DMT-1110` | Public class has no docstring. | disabled | error | error | - |
| `DMT-1120` | Public function has no docstring. | disabled | error | error | - |
| `DMT-1130` | Public method has no docstring. | disabled | error | error | - |
| `DMT-1140` | Public property has no docstring. | disabled | error | error | - |
| `DMT-1150` | Public attribute has no docstring. | disabled | error | error | - |
| `DMT-1201` | Protected module has no docstring. | disabled | warning | error | - |
| `DMT-1210` | Protected class has no docstring. | disabled | warning | error | - |
| `DMT-1220` | Protected function has no docstring. | disabled | warning | error | - |
| `DMT-1230` | Protected method has no docstring. | disabled | warning | error | - |
| `DMT-1240` | Protected property has no docstring. | disabled | warning | error | - |
| `DMT-1250` | Protected attribute has no docstring. | disabled | warning | error | - |
| `DMT-1301` | Private module has no docstring. | disabled | disabled | error | - |
| `DMT-1310` | Private class has no docstring. | disabled | disabled | error | - |
| `DMT-1320` | Private function has no docstring. | disabled | disabled | error | - |
| `DMT-1330` | Private method has no docstring. | disabled | disabled | error | - |
| `DMT-1340` | Private property has no docstring. | disabled | disabled | error | - |
| `DMT-1350` | Private attribute has no docstring. | disabled | disabled | error | - |

### 2xxx - Parameters

| Code | Meaning | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- |
| `DMT-2001` | Parameter is undocumented. | warning | error | error | ✓ |
| `DMT-2002` | Documented parameter is absent from the signature. | error | error | error | ✓ |
| `DMT-2003` | Inconsistent parameter order. | warning | warning | error | ✓ |
| `DMT-2120` | Parameter type is absent from the docstring. | disabled | warning | error | ✓ |

### 3xxx - Produced values

| Code | Meaning | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- |
| `DMT-3001` | Return value is undocumented. | warning | error | error | ✓ |
| `DMT-3010` | Return type is absent from the docstring. | disabled | warning | error | ✓ |

### 4xxx - Exceptions

| Code | Meaning | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- |
| `DMT-4001` | Raised exception is undocumented. | warning | error | error | ✓ |
| `DMT-4002` | Documented exception has no detected raise source. | error | error | error | ✓ |
| `DMT-4101` | Missing description for an exception in Raises. | disabled | warning | warning | - |
| `DMT-4102` | Invalid exception name in Raises. | disabled | warning | error | - |
| `DMT-4201` | Exception exposed after a change is not aligned with Raises (DIA). | error | error | error | ✓ |
| `DMT-4202` | Documented exception removed from behavior (DIA). | warning | warning | error | ✓ |
| `DMT-4301` | Raised exception is undocumented in a class-level aggregate. | disabled | warning | error | - |

### 5xxx - Structured objects

| Code | Meaning | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- |
| `DMT-5201` | Method origin or override changed without corresponding documentation (DIA). | warning | warning | error | ✓ |
| `DMT-5202` | Inheritance changed without corresponding documentation (DIA). | warning | warning | error | ✓ |

### 6xxx - Representation and docstyles

| Code | Meaning | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- |
| `DMT-6000` | Inconsistent indentation. | disabled | warning | warning | - |
| `DMT-6049` | Section is probably misspelled. | disabled | warning | warning | - |
| `DMT-6051` | Missing blank line. | disabled | warning | warning | - |
| `DMT-6200` | Incorrect underline length. | disabled | warning | warning | - |
| `DMT-6201` | Unknown section. | disabled | warning | warning | - |
| `DMT-6202` | Section order is invalid. | disabled | warning | warning | - |
| `DMT-6210` | Missing colon in a parameter entry. | disabled | warning | warning | - |
| `DMT-6211` | Empty parameter name. | disabled | warning | warning | - |
| `DMT-6212` | Incorrect spacing after a colon. | disabled | warning | warning | - |
| `DMT-6218` | Missing type for a parameter. | disabled | warning | warning | - |
| `DMT-6220` | Returns type and description are reversed. | disabled | warning | warning | - |
| `DMT-6240` | Raises type and description are on the same line. | disabled | warning | warning | - |

### 7xxx - Quality and semantics

| Code | Meaning | Loose | Standard | Strict | Fixable |
| --- | --- | --- | --- | --- | --- |
| `DMT-7001` | Missing or empty summary. | disabled | warning | warning | - |
| `DMT-7302` | I/O effect changed without corresponding documentation (DIA). | warning | warning | error | ✓ |
| `DMT-7303` | Observable property changed without corresponding documentation (DIA). | warning | warning | error | ✓ |
