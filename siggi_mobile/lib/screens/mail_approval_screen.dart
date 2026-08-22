import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

/// Mirrors the desktop dashboard's mail-draft approval flow: Siggi drafts a
/// reply, nothing gets sent until it's approved here (or edited first).
class MailApprovalScreen extends StatefulWidget {
  const MailApprovalScreen({super.key});
  @override
  State<MailApprovalScreen> createState() => _MailApprovalScreenState();
}

class _MailApprovalScreenState extends State<MailApprovalScreen> {
  List _drafts = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final result = await Api.instance.get('/api/mail-drafts');
      _drafts = (result?['drafts'] as List?) ?? [];
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _approve(int id, {String? to, String? subject, String? body}) async {
    if (to != null || subject != null || body != null) {
      await Api.instance.post('/api/mail-drafts/$id/edit', {
        if (to != null) 'to_addr': to,
        if (subject != null) 'subject': subject,
        if (body != null) 'body': body,
      });
    }
    final res = await Api.instance.post('/api/mail-drafts/$id/approve');
    _toast(res?['success'] == true ? 'Gesendet' : 'Fehler: ${res?['error'] ?? 'unbekannt'}');
    _load();
  }

  Future<void> _reject(int id) async {
    await Api.instance.post('/api/mail-drafts/$id/reject');
    _toast('Abgelehnt');
    _load();
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg), backgroundColor: context.colors.surface2));
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      appBar: AppBar(title: const Text('Mail-Freigabe')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _drafts.isEmpty
                ? Center(child: Text('Keine offenen Mail-Entwürfe.', style: TextStyle(color: c.dim)))
                : ListView.builder(
                    padding: const EdgeInsets.all(16),
                    itemCount: _drafts.length,
                    itemBuilder: (context, i) => _DraftCard(
                      draft: _drafts[i],
                      onApprove: _approve,
                      onReject: _reject,
                    ),
                  ),
      ),
    );
  }
}

class _DraftCard extends StatefulWidget {
  final Map draft;
  final Future<void> Function(int id, {String? to, String? subject, String? body}) onApprove;
  final Future<void> Function(int id) onReject;
  const _DraftCard({required this.draft, required this.onApprove, required this.onReject});

  @override
  State<_DraftCard> createState() => _DraftCardState();
}

class _DraftCardState extends State<_DraftCard> {
  bool _editing = false;
  late final _to = TextEditingController(text: widget.draft['to_addr']?.toString() ?? '');
  late final _subject = TextEditingController(text: widget.draft['subject']?.toString() ?? '');
  late final _body = TextEditingController(text: widget.draft['body']?.toString() ?? '');

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final d = widget.draft;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Expanded(child: Text('An: ${d['to_addr'] ?? '?'}', style: const TextStyle(color: accent, fontSize: 12, fontWeight: FontWeight.w700))),
              IconButton(
                icon: Icon(_editing ? Icons.close : Icons.edit_outlined, size: 18, color: c.dim),
                onPressed: () => setState(() => _editing = !_editing),
              ),
            ]),
            if (_editing) ...[
              TextField(controller: _to, style: TextStyle(color: c.text, fontSize: 12), decoration: const InputDecoration(labelText: 'An')),
              const SizedBox(height: 8),
              TextField(controller: _subject, style: TextStyle(color: c.text, fontSize: 13), decoration: const InputDecoration(labelText: 'Betreff')),
              const SizedBox(height: 8),
              TextField(controller: _body, style: TextStyle(color: c.text, fontSize: 13), maxLines: 6, decoration: const InputDecoration(labelText: 'Text')),
            ] else ...[
              const SizedBox(height: 6),
              Text(d['subject']?.toString() ?? 'Kein Betreff', style: TextStyle(color: c.text, fontWeight: FontWeight.w700, fontSize: 13)),
              const SizedBox(height: 6),
              Text(d['body']?.toString() ?? '', style: TextStyle(color: c.dim, fontSize: 12), maxLines: 6, overflow: TextOverflow.ellipsis),
            ],
            const SizedBox(height: 14),
            Row(children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: () => widget.onReject(d['id']),
                  style: OutlinedButton.styleFrom(foregroundColor: danger, side: const BorderSide(color: danger)),
                  child: const Text('Ablehnen'),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: ElevatedButton(
                  onPressed: () => widget.onApprove(d['id'], to: _editing ? _to.text : null, subject: _editing ? _subject.text : null, body: _editing ? _body.text : null),
                  child: const Text('Freigeben & senden'),
                ),
              ),
            ]),
          ],
        ),
      ),
    );
  }
}
