# Presentation and Defense Notes

Use DEFENSE_WALKTHROUGH.md for executable Docker commands and expected outputs. The root defense.sql file contains the numbered interactive walkthrough; the two session files demonstrate the actual last-item lock wait.

## Suggested presentation flow

1. **Requirements and invariant, 45 seconds.** Explain the 2 million products, 100 warehouses, and 10,000 updates per second. State that reserved must never exceed on_hand.
2. **Architecture and schema, 90 seconds.** Show the primary, replicas, expiry worker, and event outbox. Walk through one inventory row and its reservation lines.
3. **Partition choice, 45 seconds.** Explain warehouse ownership, the composite key, and the difference between partitioning and sharding.
4. **Concurrency challenge, 90 seconds.** Trace two buyers through the conditional UPDATE. Explain why the waiting transaction cannot reuse the old availability.
5. **Reservation lifecycle, 60 seconds.** Describe active, confirmed, released, and expired; discuss duplicate requests and the database clock.
6. **Failure recovery, 90 seconds.** Cover lost responses, stale replicas, primary failure, and lost or repeated broker messages.
7. **Multiple warehouses and limitations, 60 seconds.** Explain one-database atomicity, future saga compensation, and why throughput still requires benchmarking.
8. **Demonstration, 60 seconds.** Run the example and show the automated test output with one winner among 20 buyers.

## Likely questions and answers

**Why not check availability with SELECT first?**

Two requests could read the same value before either writes. The availability predicate must be part of the locked UPDATE, or a preceding SELECT must lock the row within the same transaction.

**Why READ COMMITTED instead of SERIALIZABLE?**

The invariant is attached to known rows, and conditional writes serialize on their locks. More complex predicates might require SERIALIZABLE, but the stated balance rule does not.

**Does partitioning create 16 independent databases?**

No. These partitions are tables under one PostgreSQL primary. Sharding means distributing data across separate writable database instances.

**Can read replicas decide whether an order succeeds?**

No. A replica may show stale quantities. The primary alone accepts the reservation; replicas serve advisory reads.

**What if the API times out after committing?**

The client retries with the same order ID and items. The primary returns the existing state. A changed payload with the same ID is rejected.

**What if confirmation and expiry arrive together?**

Both require the same reservation lock. The first admitted terminal transition wins. Deadline checks occur after stock locks are acquired. A late confirmation expires the reservation instead of consuming its stock.

**Why not hold a transaction open until payment finishes?**

Customer and network delays would retain connections and locks, increase contention, and complicate failure recovery. A committed reservation represents the temporary business hold.

**What if a captured payment arrives after expiration?**

Do not revive the old reservation. Refund or attempt a new explicit allocation. Payment and inventory need reconciliation because an external provider is not inside the PostgreSQL transaction.

**What if one warehouse fails after another reserved stock?**

Inside the baseline database, the transaction rolls back every line. Across shards, the coordinator records progress and releases previous holds. Partial confirmations require audited compensation before fulfillment.

**Can a lost shipment simply restore the source balance?**

No. Stock left the source physically. Record the loss; only verified returned goods may restore available stock.

**Do the tests prove 10,000 updates per second?**

The functional tests prove specific scenarios. A separate measured benchmark uses two million products and 100 warehouses: 64-row batches exceeded 10,000 updates/s with synchronous WAL acknowledgement. Single adjustments, checkout and a hot row have lower measured capacity. This is not a claim of 10,000 individual orders/s or an end-to-end soak test.

**What is the main bottleneck for a flash sale?**

One warehouse-product row is deliberately serialized. Queueing and admission control protect the service. Independent database copies of the same stock would break the guarantee.

**What exactly is implemented?**

The schema, reservation and transfer lifecycles, idempotent adjustments and batches, continuous expiry, leased outbox delivery, durable consumer inbox, restricted application permissions, connection pooling and retries are implemented. Docker deploys a persistent primary, synchronous standby and separate projection database. Fresh reads validate replay; explicit advisory reads can bypass primary availability. Backup restoration, primary crash recovery and fenced failover with replacement standby are tested. Physical sharding and unattended leader election remain explicit deployment decisions.


**What does synchronous standby failure look like?**

COMMIT remains pending until the required standby acknowledges WAL. Statement timeout is not a complete commit deadline. A lost result is unknown; use the original idempotency key. This tradeoff favors stock consistency over uninterrupted writes.

**What exactly does the consumer display?**

Cumulative changes from delivered events, not authoritative stock including opening balances. It applies every deduplicated delta, including out-of-order events. Purchase decisions never use this projection.

**What are the failure-domain limits?**

The lab demonstrates a single-primary failure protocol on one Docker host. Whole-host failure requires independent nodes and off-host backups. Observed recovery times are in the result files and are not production SLAs.

**How should the group prepare?**

Both participants must independently explain the invariant, the waiting UPDATE, the difference between synchronous receipt and replay, and the exact benchmark scope. The assignment permits one or two students; technical artifacts cannot substitute for participation.
