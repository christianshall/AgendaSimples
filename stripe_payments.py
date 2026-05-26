"""Rotas Flask: Stripe Checkout, callbacks e webhooks."""
from datetime import datetime, timedelta, timezone

import stripe
from flask import flash, redirect, render_template, request, session, url_for

import config_saas as cfg
from subscriptions import (
    assinatura_por_customer_id,
    assinatura_por_subscription_id,
    atualizar_assinatura_por_stripe,
    obter_assinatura,
)


def register_stripe_routes(app, get_connection):
    """Registra rotas de pagamento no app Flask."""

    def _init_stripe():
        if cfg.STRIPE_SECRET_KEY:
            stripe.api_key = cfg.STRIPE_SECRET_KEY

    def _stripe_ts_para_datetime(ts):
        if ts is None:
            return None
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)

    def _dias_trial_restantes(assinatura):
        if not assinatura or not assinatura.get("data_fim_trial"):
            return cfg.TRIAL_DAYS
        agora = datetime.utcnow()
        fim = assinatura["data_fim_trial"]
        if fim <= agora:
            return 0
        return max(0, (fim - agora).days)

    @app.route("/checkout", methods=["GET", "POST"])
    def checkout():
        if session.get("role") != "admin":
            return redirect(url_for("login"))

        barbearia_id = session.get("barbearia_id") or session.get("user_id")
        if not barbearia_id:
            return redirect(url_for("login"))

        if not cfg.STRIPE_SECRET_KEY or not cfg.STRIPE_PRICE_ID:
            flash(
                "Pagamentos não configurados. Defina STRIPE_SECRET_KEY e STRIPE_PRICE_ID.",
                "danger",
            )
            return render_template(
                "checkout.html",
                stripe_publishable_key=cfg.STRIPE_PUBLISHABLE_KEY,
                configurado=False,
                trial_days=cfg.TRIAL_DAYS,
            )

        _init_stripe()
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT nome, email FROM barbearias WHERE id = ?",
            (barbearia_id,),
        )
        barbearia = cursor.fetchone()
        if not barbearia:
            conn.close()
            return redirect(url_for("login"))

        nome_barbearia, email = barbearia[0], barbearia[1]
        assinatura = obter_assinatura(cursor, barbearia_id)
        dias_trial = _dias_trial_restantes(assinatura)

        customer_id = assinatura["stripe_customer_id"] if assinatura else None
        if not customer_id:
            customer = stripe.Customer.create(
                email=email,
                name=nome_barbearia,
                metadata={"barbearia_id": str(barbearia_id)},
            )
            customer_id = customer.id
            if assinatura:
                atualizar_assinatura_por_stripe(
                    cursor, barbearia_id, stripe_customer_id=customer_id
                )
            else:
                agora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                fim_trial = (
                    datetime.utcnow() + timedelta(days=cfg.TRIAL_DAYS)
                ).strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute(
                    """
                    INSERT INTO assinaturas (
                        barbearia_id, stripe_customer_id, plano_status,
                        data_fim_trial, criado_em, atualizado_em
                    )
                    VALUES (?, ?, 'trialing', ?, ?, ?)
                    """,
                    (barbearia_id, customer_id, fim_trial, agora, agora),
                )
            conn.commit()

        subscription_data = {"metadata": {"barbearia_id": str(barbearia_id)}}
        if dias_trial > 0:
            subscription_data["trial_period_days"] = dias_trial

        try:
            checkout_session = stripe.checkout.Session.create(
                customer=customer_id,
                mode="subscription",
                payment_method_types=["card"],
                line_items=[{"price": cfg.STRIPE_PRICE_ID, "quantity": 1}],
                subscription_data=subscription_data,
                success_url=f"{cfg.APP_BASE_URL}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{cfg.APP_BASE_URL}/payment/cancel",
                metadata={"barbearia_id": str(barbearia_id)},
            )
        except stripe.error.StripeError as e:
            conn.close()
            flash(f"Erro ao iniciar pagamento: {e.user_message or str(e)}", "danger")
            return render_template(
                "checkout.html",
                stripe_publishable_key=cfg.STRIPE_PUBLISHABLE_KEY,
                configurado=True,
                trial_days=dias_trial,
            )

        conn.close()
        return redirect(checkout_session.url, code=303)

    @app.route("/payment/success")
    def payment_success():
        if session.get("role") != "admin":
            return redirect(url_for("login"))

        barbearia_id = session.get("barbearia_id") or session.get("user_id")
        session_id = request.args.get("session_id")

        if session_id and cfg.STRIPE_SECRET_KEY:
            _init_stripe()
            try:
                cs = stripe.checkout.Session.retrieve(
                    session_id, expand=["subscription"]
                )
                sub = cs.subscription
                if sub and barbearia_id:
                    conn = get_connection()
                    cursor = conn.cursor()
                    atualizar_assinatura_por_stripe(
                        cursor,
                        barbearia_id,
                        stripe_customer_id=cs.customer,
                        stripe_subscription_id=sub.id,
                        plano_status=sub.status,
                        data_fim_trial=_stripe_ts_para_datetime(sub.trial_end),
                        data_fim_plano=_stripe_ts_para_datetime(
                            sub.current_period_end
                        ),
                    )
                    conn.commit()
                    conn.close()
            except stripe.error.StripeError:
                pass

        flash("Pagamento confirmado! Sua assinatura está ativa.", "success")
        return render_template("payment_success.html")

    @app.route("/payment/cancel")
    def payment_cancel():
        flash(
            "Pagamento cancelado. Você pode tentar novamente quando quiser.",
            "info",
        )
        return render_template("payment_cancel.html")

    @app.route("/webhook/stripe", methods=["POST"])
    def stripe_webhook():
        payload = request.get_data()
        sig_header = request.headers.get("Stripe-Signature", "")

        if not cfg.STRIPE_WEBHOOK_SECRET:
            return "Webhook secret não configurado", 500

        _init_stripe()
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, cfg.STRIPE_WEBHOOK_SECRET
            )
        except ValueError:
            return "Payload inválido", 400
        except stripe.error.SignatureVerificationError:
            return "Assinatura inválida", 400

        conn = get_connection()
        cursor = conn.cursor()
        try:
            _processar_evento_stripe(cursor, event, _init_stripe, _stripe_ts_para_datetime)
            conn.commit()
        finally:
            conn.close()

        return "", 200


def _processar_evento_stripe(cursor, event, _init_stripe, _stripe_ts_para_datetime):
    tipo = event["type"]
    data = event["data"]["object"]

    if tipo == "checkout.session.completed":
        barbearia_id = data.get("metadata", {}).get("barbearia_id")
        if barbearia_id:
            atualizar_assinatura_por_stripe(
                cursor,
                int(barbearia_id),
                stripe_customer_id=data.get("customer"),
                stripe_subscription_id=data.get("subscription"),
            )

    elif tipo in (
        "customer.subscription.updated",
        "customer.subscription.created",
    ):
        _sync_subscription(cursor, data, _stripe_ts_para_datetime)

    elif tipo == "customer.subscription.deleted":
        sub_id = data.get("id")
        barbearia_id = assinatura_por_subscription_id(cursor, sub_id)
        if not barbearia_id:
            barbearia_id = assinatura_por_customer_id(cursor, data.get("customer"))
        if barbearia_id:
            atualizar_assinatura_por_stripe(
                cursor,
                barbearia_id,
                plano_status="canceled",
                stripe_subscription_id=sub_id,
            )

    elif tipo == "invoice.payment_succeeded":
        sub_id = data.get("subscription")
        if sub_id:
            _init_stripe()
            sub = stripe.Subscription.retrieve(sub_id)
            _sync_subscription(cursor, sub, _stripe_ts_para_datetime)

    elif tipo == "invoice.payment_failed":
        sub_id = data.get("subscription")
        barbearia_id = assinatura_por_subscription_id(cursor, sub_id)
        if barbearia_id:
            atualizar_assinatura_por_stripe(
                cursor, barbearia_id, plano_status="past_due"
            )


def _sync_subscription(cursor, sub, _stripe_ts_para_datetime):
    barbearia_id = None
    meta = sub.get("metadata") or {}
    if meta.get("barbearia_id"):
        barbearia_id = int(meta["barbearia_id"])
    if not barbearia_id:
        barbearia_id = assinatura_por_subscription_id(cursor, sub.get("id"))
    if not barbearia_id:
        barbearia_id = assinatura_por_customer_id(cursor, sub.get("customer"))
    if not barbearia_id:
        return

    atualizar_assinatura_por_stripe(
        cursor,
        barbearia_id,
        stripe_customer_id=sub.get("customer"),
        stripe_subscription_id=sub.get("id"),
        plano_status=sub.get("status"),
        data_fim_trial=_stripe_ts_para_datetime(sub.get("trial_end")),
        data_fim_plano=_stripe_ts_para_datetime(sub.get("current_period_end")),
    )
