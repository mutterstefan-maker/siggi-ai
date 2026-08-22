import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

class ContactsScreen extends StatefulWidget {
  const ContactsScreen({super.key});
  @override
  State<ContactsScreen> createState() => _ContactsScreenState();
}

class _ContactsScreenState extends State<ContactsScreen> {
  List _contacts = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      _contacts = (await Api.instance.get('/api/contacts') as List?) ?? [];
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _delete(int id) async {
    await Api.instance.post('/api/contacts/$id/delete');
    _load();
  }

  Future<void> _openEditor({Map? existing}) async {
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _ContactEditorSheet(existing: existing),
    );
    if (saved == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      appBar: AppBar(title: const Text('Kontakte')),
      floatingActionButton: FloatingActionButton(
        backgroundColor: accent,
        onPressed: () => _openEditor(),
        child: const Icon(Icons.person_add_alt_1, color: Colors.white),
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _contacts.isEmpty
                ? Center(child: Text('Noch keine Kontakte. Unten rechts hinzufügen.', style: TextStyle(color: c.dim)))
                : ListView.builder(
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 80),
                    itemCount: _contacts.length,
                    itemBuilder: (context, i) {
                      final k = _contacts[i];
                      final name = (k['name'] ?? '').toString();
                      final initial = name.isNotEmpty ? name[0].toUpperCase() : '?';
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: GlassCard(
                          onTap: () => _openEditor(existing: k),
                          child: Row(children: [
                            CircleAvatar(backgroundColor: accent.withValues(alpha: 0.2), child: Text(initial, style: const TextStyle(color: accent, fontWeight: FontWeight.w800))),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(name.isEmpty ? k['email']?.toString() ?? '?' : name, style: TextStyle(color: c.text, fontWeight: FontWeight.w700)),
                                  if ((k['company'] ?? '').toString().isNotEmpty)
                                    Text(k['company'].toString(), style: TextStyle(color: c.dim, fontSize: 12)),
                                  Text(k['email']?.toString() ?? '', style: TextStyle(color: c.dim, fontSize: 11)),
                                ],
                              ),
                            ),
                            IconButton(icon: Icon(Icons.delete_outline, color: c.dim, size: 20), onPressed: () => _delete(k['id'])),
                          ]),
                        ),
                      );
                    },
                  ),
      ),
    );
  }
}

class _ContactEditorSheet extends StatefulWidget {
  final Map? existing;
  const _ContactEditorSheet({this.existing});
  @override
  State<_ContactEditorSheet> createState() => _ContactEditorSheetState();
}

class _ContactEditorSheetState extends State<_ContactEditorSheet> {
  late final _name = TextEditingController(text: widget.existing?['name'] ?? '');
  late final _email = TextEditingController(text: widget.existing?['email'] ?? '');
  late final _phone = TextEditingController(text: widget.existing?['phone'] ?? '');
  late final _company = TextEditingController(text: widget.existing?['company'] ?? '');
  late final _notes = TextEditingController(text: widget.existing?['notes'] ?? '');
  bool _saving = false;

  Future<void> _save() async {
    if (_email.text.trim().isEmpty) return;
    setState(() => _saving = true);
    final body = {
      'name': _name.text.trim(),
      'email': _email.text.trim(),
      'phone': _phone.text.trim(),
      'company': _company.text.trim(),
      'notes': _notes.text.trim(),
    };
    final id = widget.existing?['id'];
    await Api.instance.post(id != null ? '/api/contacts/$id' : '/api/contacts', body);
    if (mounted) Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: Container(
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(color: c.bg, borderRadius: const BorderRadius.vertical(top: Radius.circular(24))),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(widget.existing == null ? 'Neuer Kontakt' : 'Kontakt bearbeiten', style: TextStyle(color: c.text, fontSize: 18, fontWeight: FontWeight.w800)),
            const SizedBox(height: 16),
            TextField(controller: _name, style: TextStyle(color: c.text), decoration: const InputDecoration(hintText: 'Name')),
            const SizedBox(height: 10),
            TextField(controller: _email, style: TextStyle(color: c.text), keyboardType: TextInputType.emailAddress, decoration: const InputDecoration(hintText: 'E-Mail *')),
            const SizedBox(height: 10),
            TextField(controller: _phone, style: TextStyle(color: c.text), keyboardType: TextInputType.phone, decoration: const InputDecoration(hintText: 'Telefon')),
            const SizedBox(height: 10),
            TextField(controller: _company, style: TextStyle(color: c.text), decoration: const InputDecoration(hintText: 'Firma')),
            const SizedBox(height: 10),
            TextField(controller: _notes, style: TextStyle(color: c.text), maxLines: 3, decoration: const InputDecoration(hintText: 'Notizen')),
            const SizedBox(height: 16),
            ElevatedButton(
              onPressed: _saving ? null : _save,
              child: Text(_saving ? 'Speichere...' : 'Speichern'),
            ),
          ],
        ),
      ),
    );
  }
}
