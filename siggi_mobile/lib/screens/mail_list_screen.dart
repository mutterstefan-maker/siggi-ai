import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

class MailListScreen extends StatefulWidget {
  const MailListScreen({super.key});
  @override
  State<MailListScreen> createState() => _MailListScreenState();
}

class _MailListScreenState extends State<MailListScreen> {
  List _mails = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final all = (await Api.instance.get('/api/mails/inbox') as List?) ?? [];
      _mails = all.where((m) => m['read'] != true).toList();
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      appBar: AppBar(title: const Text('Posteingang')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _mails.isEmpty
                ? Center(child: Text('Keine ungelesenen Mails.', style: TextStyle(color: c.dim)))
                : ListView.builder(
                    padding: const EdgeInsets.all(16),
                    itemCount: _mails.length,
                    itemBuilder: (context, i) {
                      final m = _mails[i];
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: GlassCard(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(m['from_addr']?.toString() ?? '?', style: const TextStyle(color: accent, fontSize: 12, fontWeight: FontWeight.w700)),
                              const SizedBox(height: 4),
                              Text(m['subject']?.toString() ?? 'Kein Betreff', style: TextStyle(color: c.text, fontSize: 13), maxLines: 2, overflow: TextOverflow.ellipsis),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
      ),
    );
  }
}
