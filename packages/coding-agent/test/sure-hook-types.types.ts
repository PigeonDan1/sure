// Type-only regression guard, enforced by `tsgo --noEmit` (npm run check).
//
// package.json exports `./hooks` from hook-types.ts, so that file is the run
// record external hook authors compile against. A field the runtime writes but
// hook-types.ts omits is a field they cannot read even though it is there, so
// the two declarations must carry the same fields.
import type { SureRunRecord as PublishedRunRecord } from "../src/core/sure/hook-types.ts";
import type { SureRunRecord as RuntimeRunRecord } from "../src/core/sure/types.ts";

type Exact<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;

export const runRecordFieldsMatch: Exact<keyof RuntimeRunRecord, keyof PublishedRunRecord> = true;
